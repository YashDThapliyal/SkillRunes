"""`skillrunes bench` — controlled replay benchmark for instruction files.

    bench run      run a suite's tasks under one or more arms
    bench learn    learn a SKILL.md from train-split transcripts (SkillRunes)
    bench oneshot  generate the one-shot /init-style CLAUDE.md baseline
    bench report   aggregate results, compare arms, optionally gate promotion

Every agent run bills the logged-in Claude subscription; the command refuses
to start if the CLI is not authenticated that way.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

import skillrunes.store as store
from skillrunes.billing import BillingError, require_subscription_auth
from skillrunes.bench.learn import generate_oneshot, learn_skill
from skillrunes.bench.report import DEFAULT_GATE_MARGIN, build_report, gate_decision
from skillrunes.bench.runner import RunConfig, load_results, run_matrix
from skillrunes.bench.suite import load_suite, parse_arm, select_tasks
from skillrunes.config import BENCH_MODEL
from skillrunes.models import BenchReport, BenchRunResult

console = Console()
err_console = Console(stderr=True)

bench_app = typer.Typer(help="Replay benchmark: measure instruction files on held-out tasks.")

_SUITE_OPTION = typer.Option(..., "--suite", help="Path to a bench suite.toml.")
_OUT_OPTION = typer.Option(None, "--out", help="Run directory (default .skillrunes/bench/<suite>).")


@bench_app.command("run")
def bench_run(
    suite_path: Path = _SUITE_OPTION,
    split: str = typer.Option("test", "--split", help="train, test, or all."),
    arm_specs: list[str] = typer.Option(["none"], "--arm", help="'none' or name=path/to/file.md (repeatable)."),
    seeds: int = typer.Option(3, "--seeds", min=1, help="Repeats per task and arm."),
    workers: int = typer.Option(4, "--workers", min=1, help="Parallel agent runs."),
    model: str = typer.Option(BENCH_MODEL, "--model", help="Model alias for agent runs."),
    feedback_rounds: int | None = typer.Option(
        None, "--feedback-rounds", min=0,
        help="Replay reviewer feedback this many times on failure (default: 1 for train, 0 for test).",
    ),
    task_ids: list[str] = typer.Option([], "--task", help="Only these task ids (repeatable)."),
    out: Path | None = _OUT_OPTION,
    keep_workspaces: bool = typer.Option(False, "--keep-workspaces"),
) -> None:
    """Run suite tasks headlessly under each arm and record results."""
    suite, tasks, arms = _load_plan(suite_path, split, task_ids, arm_specs)
    _require_subscription()
    run_dir = (out or store.bench_dir(suite.name)).resolve()
    rounds = feedback_rounds if feedback_rounds is not None else (1 if split == "train" else 0)
    config = RunConfig(model=model, out_dir=run_dir, feedback_rounds=rounds, keep_workspaces=keep_workspaces)

    done = {r.key for r in load_results(run_dir)}
    total = len(tasks) * len(arms) * seeds
    pending = sum(
        1 for s in range(seeds) for t in tasks for a in arms if f"{t.id}|{a.name}|{s}" not in done
    )
    console.print(
        f"[bold]{suite.name}[/bold] · split={split} · arms={', '.join(a.name for a in arms)} · "
        f"seeds={seeds} · model={model} · billing=[green]subscription[/green]\n"
        f"{pending} of {total} runs pending → {run_dir}"
    )
    run_matrix(
        suite, tasks, arms, seeds, config, workers,
        on_result=_print_result,
        on_error=lambda key, exc: err_console.print(f"[red]infra error[/red] {key}: {exc}"),
    )
    _print_report(build_report(load_results(run_dir), suite.name, split, baseline="none"))


@bench_app.command("learn")
def bench_learn(
    out: Path | None = _OUT_OPTION,
    suite_name: str = typer.Option("invoicing", "--suite-name", help="Suite name when --out is omitted."),
    write: Path | None = typer.Option(None, "--write", help="Where to write the learned skill."),
    budget_tokens: int | None = typer.Option(None, "--budget-tokens", min=1),
    workers: int = typer.Option(4, "--workers", min=1),
    synthesis: str = typer.Option("rewrite", "--synthesis", help="rewrite or delta (ACE-style)."),
) -> None:
    """Learn a SKILL.md from train-split transcripts using the SkillRunes pipeline."""
    if synthesis not in ("rewrite", "delta"):
        err_console.print("[red]Error:[/red] --synthesis must be rewrite or delta.")
        raise typer.Exit(1)
    _require_subscription()
    run_dir = (out or store.bench_dir(suite_name)).resolve()
    try:
        content, summaries = learn_skill(
            load_results(run_dir), budget_tokens=budget_tokens, workers=workers, synthesis=synthesis
        )
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    default_name = "skillrunes-skill.md" if synthesis == "rewrite" else "skillrunes-delta-skill.md"
    path = store.write_text_file(write or run_dir / default_name, content)
    console.print(f"[green]✓[/green] Learned skill from {len(summaries)} session(s) → {path}")


@bench_app.command("oneshot")
def bench_oneshot(
    suite_path: Path = _SUITE_OPTION,
    model: str = typer.Option(BENCH_MODEL, "--model"),
    write: Path | None = typer.Option(None, "--write", help="Where to write the baseline file."),
    out: Path | None = _OUT_OPTION,
) -> None:
    """Generate the one-shot /init-style CLAUDE.md baseline for a suite."""
    suite = _load_suite_or_exit(suite_path)
    _require_subscription()
    try:
        content = generate_oneshot(suite, model)
    except RuntimeError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    path = store.write_text_file(write or (out or store.bench_dir(suite.name)) / "oneshot-claude.md", content)
    console.print(f"[green]✓[/green] One-shot baseline written → {path}")


@bench_app.command("report")
def bench_report(
    out: Path | None = _OUT_OPTION,
    suite_name: str = typer.Option("invoicing", "--suite-name"),
    split: str = typer.Option("test", "--split"),
    baseline: str = typer.Option("none", "--baseline", help="Arm to compare against."),
    gate: str | None = typer.Option(None, "--gate", help="Exit non-zero unless this arm passes the gate."),
    margin: float = typer.Option(DEFAULT_GATE_MARGIN, "--margin", min=0.0, max=1.0),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Summarise results, compare arms with paired bootstrap CIs, optionally gate."""
    run_dir = (out or store.bench_dir(suite_name)).resolve()
    results = load_results(run_dir)
    if not results:
        err_console.print(f"[red]Error:[/red] No results in {run_dir}.")
        raise typer.Exit(1)
    report = build_report(results, suite_name, split, baseline=baseline)
    path = store.save_bench_report(run_dir, report)
    if as_json:
        console.print_json(report.model_dump_json())
    else:
        _print_report(report)
        console.print(f"[dim]Report saved to {path}[/dim]")
    if gate:
        promote, reason = gate_decision(report, gate, margin)
        verdict = "[green]PROMOTE[/green]" if promote else "[red]REJECT[/red]"
        console.print(f"Gate for [bold]{gate}[/bold] vs {baseline}: {verdict} — {reason}")
        if not promote:
            raise typer.Exit(2)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_suite_or_exit(path: Path):
    try:
        return load_suite(path)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc


def _load_plan(suite_path: Path, split: str, task_ids: list[str], arm_specs: list[str]):
    suite = _load_suite_or_exit(suite_path)
    try:
        tasks = select_tasks(suite, split, task_ids or None)
        arms = [parse_arm(spec) for spec in arm_specs]
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    names = [a.name for a in arms]
    if len(set(names)) != len(names):
        err_console.print("[red]Error:[/red] Arm names must be unique.")
        raise typer.Exit(1)
    return suite, tasks, arms


def _require_subscription() -> None:
    try:
        require_subscription_auth()
    except BillingError as exc:
        err_console.print(f"[red]Billing guard:[/red] {exc}")
        raise typer.Exit(1) from exc


def _print_result(result: BenchRunResult) -> None:
    mark = "[green]pass[/green]" if result.passed else "[red]fail[/red]"
    first = result.attempts[0] if result.attempts else None
    detail = f"{first.num_turns} turns, {first.total_tokens:,} tok" if first else "no attempt"
    retry = " (after feedback)" if result.passed and not result.first_attempt_passed else ""
    console.print(f"  {mark}{retry} {result.task_id} · {result.arm} · seed {result.seed} · {detail}")


def _print_report(report: BenchReport) -> None:
    if not report.arms:
        console.print("[yellow]No results for this split yet.[/yellow]")
        return
    table = Table(title=f"{report.suite} · {report.split} split")
    for column in ("Arm", "Runs", "Pass rate [95% CI]", "Turns", "Tokens", "Tool errors", "Agent errors"):
        table.add_column(column, justify="left" if column == "Arm" else "right")
    for arm in report.arms:
        table.add_row(
            arm.arm,
            str(arm.runs),
            f"{arm.pass_rate:.0%} [{arm.pass_rate_ci[0]:.0%}, {arm.pass_rate_ci[1]:.0%}]",
            f"{arm.mean_turns:.1f}",
            f"{arm.mean_tokens:,.0f}",
            f"{arm.mean_tool_errors:.2f}",
            str(arm.agent_errors),
        )
    console.print(table)

    criteria = sorted({name for arm in report.arms for name in arm.criteria_rates})
    if criteria:
        ctable = Table(title="Reviewer criteria pass rate")
        ctable.add_column("Arm")
        for name in criteria:
            ctable.add_column(name.replace("_", " "), justify="right")
        for arm in report.arms:
            ctable.add_row(arm.arm, *(f"{arm.criteria_rates.get(n, 0):.0%}" for n in criteria))
        console.print(ctable)

    for comp in report.comparisons:
        console.print(
            f"[bold]{comp.candidate}[/bold] vs {comp.baseline} ({comp.pairs} paired runs): "
            f"pass {comp.pass_rate_delta:+.1%} [{comp.pass_rate_ci[0]:+.1%}, {comp.pass_rate_ci[1]:+.1%}] · "
            f"tokens {comp.tokens_delta:+,.0f} [{comp.tokens_ci[0]:+,.0f}, {comp.tokens_ci[1]:+,.0f}] · "
            f"turns {comp.turns_delta:+.1f} [{comp.turns_ci[0]:+.1f}, {comp.turns_ci[1]:+.1f}]"
        )

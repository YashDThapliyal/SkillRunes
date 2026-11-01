"""Measurement commands: `skillrunes impact`.

Kept separate from cli.py so that module stays focused on the ingest →
synthesise pipeline.  Commands are attached to the main Typer app via
register().  No model calls are made here; all I/O goes through store.py.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

import skillrunes.store as store
from skillrunes.impact import DEFAULT_MIN_SESSIONS, backfill_summary, build_impact_report
from skillrunes.ingest import get_provider
from skillrunes.models import ImpactReport, SessionSummary

console = Console()
err_console = Console(stderr=True)

_VERDICT_STYLE = {
    "improved": "[green]improved[/green]",
    "regressed": "[red]regressed[/red]",
    "inconclusive": "[yellow]inconclusive[/yellow]",
    "insufficient_data": "[dim]too few sessions[/dim]",
}
_DEFAULT_PROVIDER_FOR_LEGACY = "claude_code"


def register(app: typer.Typer) -> None:
    """Attach measurement commands to the main CLI app."""
    app.command("impact")(impact)
    from skillrunes.cli_bench import bench_app

    app.add_typer(bench_app, name="bench")


def impact(
    min_sessions: int = typer.Option(
        DEFAULT_MIN_SESSIONS,
        "--min-sessions",
        min=1,
        help="Minimum sessions per cohort before a confidence interval is computed.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """Measure how agent behaviour changed after each SKILL.md version."""
    store.init_store()
    summaries = backfill_all(store.load_all_summaries())
    history = store.load_skill_history()
    report = build_impact_report(summaries, history, min_sessions=min_sessions)
    path = store.save_impact_report(report)

    if as_json:
        console.print_json(report.model_dump_json())
        return
    if not report.cohorts:
        console.print(
            "[yellow]No measurable sessions yet.[/yellow] Run [bold]skillrunes run[/bold] "
            "to analyse sessions first."
        )
        return
    _print_cohorts(report)
    _print_deltas(report)
    if report.unmeasured_sessions:
        console.print(
            f"[dim]{report.unmeasured_sessions} session(s) had no raw archive to measure.[/dim]"
        )
    console.print(f"[green]✓[/green] Impact report written to {path}")


def backfill_all(summaries: list[SessionSummary]) -> list[SessionSummary]:
    """Rebuild missing signals from raw archives and persist updated summaries."""
    updated: list[SessionSummary] = []
    for summary in summaries:
        provider_name = summary.provider or _DEFAULT_PROVIDER_FOR_LEGACY
        try:
            provider = get_provider(provider_name)
        except ValueError:
            updated.append(summary)
            continue
        refreshed = backfill_summary(
            summary,
            store.load_raw_session(summary.session_id),
            provider.messages_from_raw,
        )
        if refreshed is None:
            updated.append(summary)
            continue
        store.save_session_summary(refreshed)
        updated.append(refreshed)
    return updated


def _print_cohorts(report: ImpactReport) -> None:
    table = Table(title="Behaviour by active skill version (mean per session, lower is better)")
    table.add_column("Version")
    table.add_column("Sessions", justify="right")
    metric_names = list(report.cohorts[0].metrics)
    for name in metric_names:
        table.add_column(name.replace("_", " "), justify="right")
    for cohort in report.cohorts:
        label = "baseline" if cohort.version == 0 else f"v{cohort.version}"
        table.add_row(
            label,
            str(len(cohort.session_ids)),
            *(_fmt(cohort.metrics[name]) for name in metric_names),
        )
    console.print(table)


def _print_deltas(report: ImpactReport) -> None:
    if not report.deltas:
        return
    table = Table(title="Change vs previous version (95% bootstrap CI)")
    table.add_column("Transition")
    table.add_column("Metric")
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Δ [CI]", justify="right")
    table.add_column("Verdict")
    for delta in report.deltas:
        ci = (
            f" [{_fmt(delta.ci_low)}, {_fmt(delta.ci_high)}]"
            if delta.ci_low is not None and delta.ci_high is not None
            else ""
        )
        before = "baseline" if delta.before_version == 0 else f"v{delta.before_version}"
        table.add_row(
            f"{before} → v{delta.after_version}",
            delta.metric,
            _fmt(delta.before_mean),
            _fmt(delta.after_mean),
            f"{delta.delta:+.3g}{ci}",
            _VERDICT_STYLE.get(delta.verdict, delta.verdict),
        )
    console.print(table)


def _fmt(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:.3g}"

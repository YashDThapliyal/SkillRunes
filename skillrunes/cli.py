"""Typer CLI commands for SkillRunes.

Three commands:
  skillrunes init       — initialise the .skillrunes/ store for this project
  skillrunes run        — analyse new sessions and update SKILL.md
  skillrunes visualize  — launch the local observability dashboard

No model calls are made from this module.  Extraction is delegated to
extract.py and synthesize.py.  All file I/O is delegated to store.py.
"""

import webbrowser
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

import skillrunes.store as store
from skillrunes.billing import BillingError, require_subscription_auth, resolve_billing_mode
from skillrunes.compress import (
    build_ranked_memory_seed,
    compress_skill_content,
    estimate_tokens,
    validate_compression_mode,
)
from skillrunes.cli_measure import register as register_measurement_commands
from skillrunes.cli_traces import register as register_trace_commands
from skillrunes.config import (
    BILLING_ENV_VAR,
    DEFAULT_PROVIDER,
    PROVIDER_ENV_VAR,
    SUPPORTED_PROVIDERS,
)
from skillrunes.evaluate import evaluate_skill_versions, latest_pair, version_pair
from skillrunes.exporters import (
    build_agents_update,
    build_claude_skill,
    diff_file,
    validate_export_target,
)
from skillrunes.extract import analyze_session
from skillrunes.ingest import get_provider
from skillrunes.ingest.base import ProviderNotImplementedError
from skillrunes.curate import apply_deltas, render_rulebook, rulebook_for_prompt
from skillrunes.models import CompressionReport, CurationReport, EvalResult, RuleBook, SkillExport
from skillrunes.redact import (
    redact_messages_and_raw,
    scan_paths,
    validate_redaction_level,
)
from skillrunes.synthesize import generate_skill_file, propose_rule_deltas
from skillrunes.utils import compute_metrics, now_utc, render_skill_diff

load_dotenv()

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="skillrunes",
    help="Analyse coding-agent sessions and recursively improve a SKILL.md file.",
    no_args_is_help=True,
)

doctor_app = typer.Typer(help="Run local health and privacy checks.")
app.add_typer(doctor_app, name="doctor")


register_measurement_commands(app)
register_trace_commands(app)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Initialise the .skillrunes/ store for the current project."""
    cwd = Path.cwd()
    store_path = cwd / ".skillrunes"

    store.init_store()

    console.print(
        Panel(
            f"[green]Initialised[/green] [bold]{store_path}[/bold]\n\n"
            "Next steps:\n"
            "  1. Copy [bold].env.example[/bold] → [bold].env[/bold] and set "
            "[bold]ANTHROPIC_API_KEY[/bold]\n"
            "     (or skip this step — SkillRunes will fall back to the Claude CLI)\n"
            "  2. Run [bold]skillrunes run[/bold] to analyse sessions and generate SKILL.md",
            title="SkillRunes initialised",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@app.command()
def run(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without writing any files."
    ),
    provider_name: str | None = typer.Option(
        None,
        "--provider",
        help=(
            "Transcript provider to use "
            f"({', '.join(SUPPORTED_PROVIDERS)}). "
            f"Defaults to ${PROVIDER_ENV_VAR} or {DEFAULT_PROVIDER}."
        ),
    ),
    redaction_level: str = typer.Option(
        "basic",
        "--redact-level",
        help="Privacy redaction level: basic, strict, or off.",
    ),
    budget_tokens: int | None = typer.Option(
        None,
        "--budget-tokens",
        help="Approximate token budget for generated SKILL.md content.",
    ),
    compression_mode: str = typer.Option(
        "balanced",
        "--compression-mode",
        help="Compression mode: concise, balanced, or detailed.",
    ),
    synthesis: str = typer.Option(
        "rewrite",
        "--synthesis",
        help=(
            "rewrite: regenerate SKILL.md as a whole. delta: ACE-style rule edits "
            "with ids, provenance, helpful/harmful votes and collapse guards."
        ),
    ),
    billing: str | None = typer.Option(
        None,
        "--billing",
        help=(
            "Model billing: auto, subscription (Claude CLI login, API keys ignored), "
            f"or api. Defaults to ${BILLING_ENV_VAR} or auto."
        ),
    ),
) -> None:
    """Analyse new sessions and update SKILL.md."""

    # ── 1. Resolve and display cwd immediately ───────────────────────────────
    cwd = Path.cwd()
    console.print(Rule(f"[bold]SkillRunes[/bold]  •  {cwd}", style="dim"))
    try:
        redaction_level = validate_redaction_level(redaction_level)
        compression_mode = validate_compression_mode(compression_mode)
        _validate_budget(budget_tokens)
        billing = resolve_billing_mode(billing)
        if synthesis not in SYNTHESIS_MODES:
            raise ValueError(f"--synthesis must be one of: {', '.join(SYNTHESIS_MODES)}")
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    try:
        provider = get_provider(provider_name)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"[dim]Active provider:[/dim] [bold]{provider.display_name}[/bold] "
        f"[dim]({provider.name})[/dim]"
    )
    if dry_run:
        console.print("[yellow]Dry run — no files will be written.[/yellow]\n")
    if redaction_level == "off":
        console.print(
            Panel(
                "[yellow]Redaction is off.[/yellow] Raw transcript text may be archived "
                "and sent to the configured model client.",
                title="Privacy warning",
                border_style="yellow",
            )
        )
    else:
        console.print(f"[dim]Redaction:[/dim] [bold]{redaction_level}[/bold]")
    console.print(f"[dim]Billing:[/dim] [bold]{billing}[/bold]")
    if billing == "subscription" and not dry_run:
        try:
            require_subscription_auth()
        except BillingError as exc:
            err_console.print(f"[red]Billing guard:[/red] {exc}")
            raise typer.Exit(1) from exc
    if budget_tokens:
        console.print(
            f"[dim]Skill budget:[/dim] [bold]{budget_tokens:,}[/bold] tokens "
            f"[dim]({compression_mode})[/dim]"
        )

    # ── 2. Ensure store is initialised ───────────────────────────────────────
    store.init_store()

    # ── 3. Find new sessions ─────────────────────────────────────────────────
    console.print(f"[dim]Scanning sessions for:[/dim] [bold]{cwd}[/bold]")
    try:
        new_session_paths = provider.find_new_sessions(cwd)
    except ProviderNotImplementedError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not new_session_paths:
        console.print(
            "\n[yellow]No new sessions found for this directory.[/yellow]\n"
            "Are you running [bold]skillrunes run[/bold] from the right project folder?\n"
            f"Expected transcripts in: [dim]{provider.expected_location(cwd)}[/dim]"
        )
        raise typer.Exit(0)

    console.print(
        f"Found [bold green]{len(new_session_paths)}[/bold green] new "
        f"{'session' if len(new_session_paths) == 1 else 'sessions'} to analyse.\n"
    )

    # ── 4. Parse + archive + extract each session ────────────────────────────
    new_summaries = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Analysing sessions…", total=len(new_session_paths))

        for path in new_session_paths:
            sid = provider.session_id_from_path(path)
            progress.update(task, description=f"Analysing [dim]{sid[:8]}…[/dim]")

            messages, raw_lines = provider.parse_session_file(path)

            if not messages:
                progress.advance(task)
                console.print(
                    f"  [yellow]Skipped[/yellow] {sid[:8]}… — no messages after filtering"
                )
                continue

            messages, raw_lines, redaction_report = redact_messages_and_raw(
                messages, raw_lines, redaction_level, cwd
            )

            # Archive raw lines before any model call so originals are preserved
            # even if the provider later deletes or rotates the transcript.
            if not dry_run:
                store.archive_raw_session(sid, raw_lines)

            try:
                summary = analyze_session(messages, sid, raw_lines, billing=billing)
                summary = summary.model_copy(
                    update={"redaction_report": redaction_report, "provider": provider.name}
                )
            except Exception as exc:
                err_console.print(
                    f"  [red]Error[/red] extracting {sid[:8]}…: {exc}"
                )
                progress.advance(task)
                continue

            new_summaries.append(summary)

            # Show a summary panel for each extracted session
            _print_session_panel(summary)
            progress.advance(task)

    if not new_summaries:
        console.print("[yellow]No sessions were successfully extracted.[/yellow]")
        raise typer.Exit(1)

    # ── 5. Synthesise updated skill file ─────────────────────────────────────
    console.print()
    console.print("[dim]Synthesising SKILL.md…[/dim]")

    current_skill_version = store.load_current_skill()
    current_skill_content = current_skill_version.content if current_skill_version else None

    new_rulebook = None
    try:
        if synthesis == "delta":
            new_rulebook, new_skill_content, change_summary = _synthesise_delta(
                new_summaries, billing
            )
        else:
            memory_seed = build_ranked_memory_seed(new_summaries, compression_mode)
            new_skill_content, change_summary = generate_skill_file(
                new_summaries, current_skill_content, memory_seed, billing=billing
            )
    except Exception as exc:
        err_console.print(f"[red]Error[/red] synthesising SKILL.md: {exc}")
        raise typer.Exit(1) from exc

    reference_content = ""
    new_skill_content, reference_content, compression_report = compress_skill_content(
        new_skill_content,
        budget_tokens=budget_tokens,
        mode=compression_mode,
    )
    _print_compression_report(compression_report)

    # ── 6. Show diff ─────────────────────────────────────────────────────────
    _print_skill_diff(current_skill_content, new_skill_content, current_skill_version)

    # ── 7. Persist (skipped on --dry-run) ────────────────────────────────────
    if dry_run:
        console.print(
            "\n[yellow]Dry run — nothing written.[/yellow] "
            "Remove [bold]--dry-run[/bold] to save."
        )
        raise typer.Exit(0)

    # Save session summaries and updated metrics
    for summary in new_summaries:
        store.save_session_summary(summary)

    all_summaries = store.load_all_summaries()
    store.save_metrics(compute_metrics(all_summaries))

    if new_rulebook is not None:
        store.save_rulebook(new_rulebook)

    # Save new skill version (archives to .skillrunes/skills/ then writes SKILL.md)
    skill_version = store.save_skill_version(
        new_skill_content,
        change_summary,
        token_estimate=estimate_tokens(new_skill_content),
        compression_report=compression_report,
        reference_content=reference_content,
    )
    console.print(
        f"\n[green]✓[/green] SKILL.md written "
        f"([bold]v{skill_version.version}[/bold]  •  "
        f"{estimate_tokens(new_skill_content):,} est. tokens)"
    )
    if reference_content:
        console.print(
            "[dim]Compression moved lower-priority detail into reference overflow "
            "for future export targets.[/dim]"
        )


# ---------------------------------------------------------------------------
# visualize
# ---------------------------------------------------------------------------

@app.command()
def visualize() -> None:
    """Launch the local observability dashboard on http://localhost:4823."""
    try:
        import uvicorn
    except ImportError:
        err_console.print(
            "[red]Error:[/red] uvicorn is not installed. "
            "Run [bold]pip install skillrunes[/bold] to install all dependencies."
        )
        raise typer.Exit(1)

    from skillrunes.config import DASHBOARD_HOST, DASHBOARD_PORT

    store.init_store()

    url = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
    console.print(
        Panel(
            f"Dashboard starting at [bold cyan]{url}[/bold cyan]\n"
            "Press [bold]Ctrl+C[/bold] to stop.",
            title="SkillRunes Dashboard",
            border_style="cyan",
        )
    )

    webbrowser.open(url)

    try:
        uvicorn.run(
            "skillrunes.dashboard.server:app",
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            log_level="warning",
        )
    except OSError as exc:
        if "address already in use" in str(exc).lower():
            err_console.print(
                f"[red]Error:[/red] Port {DASHBOARD_PORT} is already in use. "
                "Kill the existing process or change DASHBOARD_PORT in config.py."
            )
            raise typer.Exit(1) from exc
        raise


# ---------------------------------------------------------------------------
# doctor privacy
# ---------------------------------------------------------------------------

@doctor_app.command("privacy")
def doctor_privacy(
    level: str = typer.Option(
        "basic",
        "--level",
        help="Privacy scan level: basic or strict.",
    )
) -> None:
    """Scan local SkillRunes artifacts for likely secrets."""
    try:
        level = validate_redaction_level(level)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if level == "off":
        err_console.print("[red]Error:[/red] doctor privacy supports only basic or strict.")
        raise typer.Exit(1)
    store.init_store()
    report = scan_paths(store.privacy_scan_paths(), Path.cwd(), level=level)
    if not report.detector_counts:
        console.print("[green]✓[/green] No likely secrets found in SkillRunes artifacts.")
        return

    table = Table(title="Privacy findings")
    table.add_column("Detector")
    table.add_column("Count", justify="right")
    for detector, count in sorted(report.detector_counts.items()):
        table.add_row(detector, str(count))
    console.print(table)
    console.print("[yellow]Files with findings:[/yellow]")
    for field in report.redacted_fields:
        console.print(f"  - {field}")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

@app.command()
def prune(
    budget_tokens: int = typer.Option(..., "--budget-tokens", help="Approximate token budget."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the diff without writing."),
    compression_mode: str = typer.Option("balanced", "--compression-mode"),
) -> None:
    """Recompress the current SKILL.md without ingesting sessions."""
    try:
        compression_mode = validate_compression_mode(compression_mode)
        _validate_budget(budget_tokens)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    store.init_store()
    current = store.load_current_skill()
    if current is None:
        err_console.print("[red]Error:[/red] No skill versions found. Run skillrunes run first.")
        raise typer.Exit(1)

    compressed, reference, report = compress_skill_content(
        current.content,
        budget_tokens=budget_tokens,
        mode=compression_mode,
    )
    _print_compression_report(report)
    _print_skill_diff(current.content, compressed, current)
    if dry_run:
        console.print("[yellow]Dry run — prune did not write a new skill version.[/yellow]")
        return
    version = store.save_skill_version(
        compressed,
        "Recompressed SkillRunes memory to fit the configured token budget.",
        token_estimate=estimate_tokens(compressed),
        compression_report=report,
        reference_content=reference,
    )
    console.print(f"[green]✓[/green] Wrote compressed SKILL.md v{version.version}")
    if reference:
        console.print("[dim]Reference overflow will be regenerated during export.[/dim]")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@app.command("export")
def export_memory(
    target: str = typer.Option(..., "--target", help="Export target: claude, agents, or all."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show diffs without writing."),
    budget_tokens: int | None = typer.Option(None, "--budget-tokens"),
    compression_mode: str = typer.Option("balanced", "--compression-mode"),
) -> None:
    """Export current memory to agent-native instruction formats."""
    try:
        target = validate_export_target(target)
        compression_mode = validate_compression_mode(compression_mode)
        _validate_budget(budget_tokens)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    store.init_store()
    current = store.load_current_skill()
    if current is None:
        err_console.print("[red]Error:[/red] No skill versions found. Run skillrunes run first.")
        raise typer.Exit(1)

    content, reference, report = compress_skill_content(
        current.content,
        budget_tokens=budget_tokens,
        mode=compression_mode,
    )
    combined_reference = "\n\n".join(
        part.strip() for part in (current.reference_content, reference) if part.strip()
    )
    skill = current.model_copy(
        update={
            "content": content,
            "token_estimate": estimate_tokens(content),
            "compression_report": report,
            "reference_content": combined_reference,
        }
    )
    targets = ("claude", "agents") if target == "all" else (target,)
    exports = []

    for item in targets:
        if item == "claude":
            files, metadata = build_claude_skill(skill, combined_reference)
            exports.extend(metadata)
            for path, new_content in files.items():
                old = store.read_project_file(path)
                if dry_run:
                    _print_export_diff(path, old, new_content)
                else:
                    store.write_project_file(path, new_content)
                    console.print(f"[green]✓[/green] Exported {path}")
        elif item == "agents":
            path = Path("AGENTS.md")
            old = store.read_project_file(path)
            new = build_agents_update(old, skill)
            exports.append(metadata_for_agents(path, skill.version, new))
            if dry_run:
                _print_export_diff(path, old, new)
            else:
                store.write_project_file(path, new)
                console.print(f"[green]✓[/green] Updated {path}")

    if not dry_run:
        store.save_skill_exports(exports)
    else:
        console.print("[yellow]Dry run — no exports were written.[/yellow]")


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------

@app.command("eval")
def eval_memory(
    latest: bool = typer.Option(False, "--latest", help="Compare the latest two skill versions."),
    before_version: int | None = typer.Option(None, "--before", help="Before version number."),
    after_version: int | None = typer.Option(None, "--after", help="After version number."),
) -> None:
    """Run deterministic memory-quality evals."""
    store.init_store()
    history = store.load_skill_history()
    try:
        before, after = (
            latest_pair(history)
            if latest or before_version is None or after_version is None
            else version_pair(history, before_version, after_version)
        )
        result = evaluate_skill_versions(before, after, store.load_all_summaries())
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    path = store.save_eval_result(result)
    _print_eval_result(result)
    console.print(f"[green]✓[/green] Eval artifact written to {path}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

SYNTHESIS_MODES = ("rewrite", "delta")


def _synthesise_delta(
    summaries: list["SessionSummary"],  # type: ignore[name-defined]
    billing: str,
) -> tuple[RuleBook, str, str]:
    """Propose + apply rule deltas; return (new book, rendered skill, summary)."""
    book = store.load_rulebook()
    deltas = propose_rule_deltas(summaries, rulebook_for_prompt(book), billing=billing)
    new_book, report = apply_deltas(book, deltas)
    _print_curation_report(report)
    change_summary = (
        f"Delta curation: +{len(report.added)} added, {len(report.updated)} updated, "
        f"{len(report.removed)} removed, {len(report.pruned)} pruned, "
        f"{len(report.merged_duplicates)} duplicate(s) merged."
    )
    return new_book, render_rulebook(new_book), change_summary


def _print_curation_report(report: CurationReport) -> None:
    parts = [
        f"[green]+{len(report.added)}[/green] added",
        f"{len(report.updated)} updated",
        f"[red]-{len(report.removed)}[/red] removed",
        f"{len(report.pruned)} pruned",
        f"{len(report.merged_duplicates)} merged",
    ]
    console.print("[dim]Curation:[/dim] " + " · ".join(parts))
    for rejected in report.rejected:
        console.print(f"  [yellow]rejected[/yellow] {rejected}")


def _print_session_panel(summary: "SessionSummary") -> None:  # type: ignore[name-defined]
    lines: list[str] = [
        f"[dim]Session:[/dim] {summary.session_id[:8]}…",
        f"[dim]Goal:[/dim]    {summary.goal}",
        f"[dim]Tokens:[/dim]  {summary.token_count:,}  "
        f"[dim]Duration:[/dim] {summary.duration_seconds:.0f}s  "
        f"[dim]Messages:[/dim] {summary.message_count}",
    ]
    if summary.what_worked:
        lines.append(
            "[green]✓ Worked:[/green] " + "; ".join(summary.what_worked[:2])
            + ("…" if len(summary.what_worked) > 2 else "")
        )
    if summary.what_failed:
        lines.append(
            "[red]✗ Failed:[/red] " + "; ".join(summary.what_failed[:2])
            + ("…" if len(summary.what_failed) > 2 else "")
        )
    if summary.patterns:
        lines.append(
            "[cyan]◆ Pattern:[/cyan] " + summary.patterns[0]
            + (f" (+{len(summary.patterns) - 1} more)" if len(summary.patterns) > 1 else "")
        )
    console.print(Panel("\n".join(lines), border_style="dim"))


def _print_skill_diff(
    old_content: str | None,
    new_content: str,
    current_version: "SkillVersion | None",  # type: ignore[name-defined]
) -> None:
    if old_content is None:
        console.print(
            Panel(
                f"[green]New SKILL.md will be created[/green] "
                f"({len(new_content)} chars)",
                title="SKILL.md  •  v1 (new)",
                border_style="green",
            )
        )
        return

    version_num = (current_version.version + 1) if current_version else 1
    diff_text = render_skill_diff(old_content, new_content, version_num)

    if not diff_text.strip():
        console.print("[dim]No changes to SKILL.md.[/dim]")
        return

    # Render unified diff with colour using Rich Syntax
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
    console.print(
        Panel(
            syntax,
            title=f"SKILL.md diff  •  v{version_num - 1} → v{version_num}",
            border_style="blue",
        )
    )


def _print_compression_report(report: CompressionReport) -> None:
    console.print(
        "[dim]Compression:[/dim] "
        f"{report.estimated_tokens_before:,} → {report.estimated_tokens_after:,} "
        f"est. tokens [dim]({report.mode}, budget {report.budget_tokens:,}; "
        f"moved {report.moved_count})[/dim]"
    )


def _validate_budget(budget_tokens: int | None) -> None:
    if budget_tokens is not None and budget_tokens <= 0:
        raise ValueError("--budget-tokens must be a positive integer.")


def _print_export_diff(path: Path, old: str, new: str) -> None:
    diff_text = diff_file(path, old, new)
    if not diff_text.strip():
        console.print(f"[dim]No changes for {path}.[/dim]")
        return
    console.print(
        Panel(
            Syntax(diff_text, "diff", theme="monokai", line_numbers=False),
            title=f"Export diff • {path}",
            border_style="blue",
        )
    )


def metadata_for_agents(path: Path, version: int, content: str) -> SkillExport:
    return SkillExport(
        target="agents",
        path=str(path),
        version=version,
        token_estimate=estimate_tokens(content),
        generated_at=now_utc(),
    )


def _print_eval_result(result: EvalResult) -> None:
    table = Table(title=f"SkillRunes eval v{result.before_version} → v{result.after_version}")
    table.add_column("Check")
    table.add_column("Result")
    for name, passed in result.checks.items():
        table.add_row(name, "[green]pass[/green]" if passed else "[red]fail[/red]")
    for name, value in result.metrics.items():
        table.add_row(name, str(value))
    console.print(table)

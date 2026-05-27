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

import skillrunes.store as store
from skillrunes.config import DEFAULT_PROVIDER, PROVIDER_ENV_VAR, SUPPORTED_PROVIDERS
from skillrunes.extract import analyze_session
from skillrunes.ingest import get_provider
from skillrunes.ingest.base import ProviderNotImplementedError
from skillrunes.synthesize import generate_skill_file
from skillrunes.utils import compute_metrics, render_skill_diff

load_dotenv()

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="skillrunes",
    help="Analyse coding-agent sessions and recursively improve a SKILL.md file.",
    no_args_is_help=True,
)


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
) -> None:
    """Analyse new sessions and update SKILL.md."""

    # ── 1. Resolve and display cwd immediately ───────────────────────────────
    cwd = Path.cwd()
    console.print(Rule(f"[bold]SkillRunes[/bold]  •  {cwd}", style="dim"))
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

            # Archive raw lines before any model call so originals are preserved
            # even if the provider later deletes or rotates the transcript.
            if not dry_run:
                store.archive_raw_session(sid, raw_lines)

            try:
                summary = analyze_session(messages, sid, raw_lines)
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

    try:
        new_skill_content, change_summary = generate_skill_file(
            new_summaries, current_skill_content
        )
    except Exception as exc:
        err_console.print(f"[red]Error[/red] synthesising SKILL.md: {exc}")
        raise typer.Exit(1) from exc

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

    # Save new skill version (archives to .skillrunes/skills/ then writes SKILL.md)
    skill_version = store.save_skill_version(new_skill_content, change_summary)
    console.print(
        f"\n[green]✓[/green] SKILL.md written "
        f"([bold]v{skill_version.version}[/bold]  •  {len(new_skill_content)} chars)"
    )

    # ── 8. Auto-copy to provider-specific skill location when supported ──────
    if provider.name == "claude_code":
        _sync_to_claude_dir(cwd, new_skill_content, dry_run=False)


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
# Internal helpers
# ---------------------------------------------------------------------------

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


def _sync_to_claude_dir(cwd: Path, skill_content: str, *, dry_run: bool) -> None:
    """Copy SKILL.md to .claude/SKILL.md if the .claude/ directory exists.

    This closes the product loop: SKILL.md is generated → Claude Code loads it
    on the next session automatically without the user having to do anything.

    If .claude/ does not exist, print a tip explaining how to enable this.
    """
    claude_dir = cwd / ".claude"

    if not claude_dir.is_dir():
        console.print(
            "\n[dim]Tip:[/dim] Create a [bold].claude/[/bold] folder in your project root "
            "and SkillRunes will automatically place SKILL.md there for Claude Code to load."
        )
        return

    if dry_run:
        console.print(
            f"\n[dim]Dry run:[/dim] would copy SKILL.md → "
            f"[bold]{claude_dir / 'SKILL.md'}[/bold]"
        )
        return

    dest = claude_dir / "SKILL.md"
    dest.write_text(skill_content, encoding="utf-8")
    console.print(
        f"[green]✓[/green] Copied SKILL.md → [bold]{dest}[/bold]  "
        f"[dim](Claude Code will load this on the next session)[/dim]"
    )

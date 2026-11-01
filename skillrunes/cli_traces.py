"""`skillrunes export-traces`: redacted trace export (SkillRunes JSON or OTLP).

Attached to the main app via register().  All I/O goes through store.py.
"""

from __future__ import annotations

import typer
from rich.console import Console

import skillrunes.store as store
from skillrunes.config import DEFAULT_PROVIDER
from skillrunes.ingest import get_provider
from skillrunes.models import RawMessage, SessionSummary
from skillrunes.otlp import build_otlp_request, post_otlp
from skillrunes.redact import validate_redaction_level
from skillrunes.traces import build_trace_exports

console = Console()
err_console = Console(stderr=True)


def register(app: typer.Typer) -> None:
    """Attach trace export commands to the main CLI app."""
    app.command("export-traces")(export_traces)


# ---------------------------------------------------------------------------
# export-traces
# ---------------------------------------------------------------------------

def export_traces(
    trace_format: str = typer.Option(
        "otel-json", "--format", help="otel-json (SkillRunes span summary) or otlp-json (OTLP GenAI traces)."
    ),
    provider: str = typer.Option("unknown", "--provider", help="Provider label for otel-json traces."),
    redaction_level: str = typer.Option(
        "basic",
        "--redact-level",
        help="Export-time redaction level: basic or strict.",
    ),
    endpoint: str | None = typer.Option(
        None, "--endpoint", help="otlp-json only: also POST to this OTLP/HTTP collector (e.g. http://localhost:4318)."
    ),
) -> None:
    """Export analysed sessions as redacted traces."""
    if trace_format not in TRACE_FORMATS:
        err_console.print(f"[red]Error:[/red] --format must be one of: {', '.join(TRACE_FORMATS)}.")
        raise typer.Exit(1)
    try:
        redaction_level = validate_redaction_level(redaction_level)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    if redaction_level == "off":
        err_console.print("[red]Error:[/red] trace export requires basic or strict redaction.")
        raise typer.Exit(1)
    if endpoint and trace_format != "otlp-json":
        err_console.print("[red]Error:[/red] --endpoint requires --format otlp-json.")
        raise typer.Exit(1)
    store.init_store()
    summaries = store.load_all_summaries()

    if trace_format == "otel-json":
        exports = build_trace_exports(summaries, provider, redaction_level=redaction_level)
        path = store.save_trace_exports(exports)
        console.print(f"[green]✓[/green] Exported {len(exports)} trace(s) to {path}")
        return

    request = build_otlp_request(
        [(s, _archived_messages(s)) for s in summaries], redaction_level=redaction_level
    )
    path = store.save_otlp_export(request)
    span_count = len(request["resourceSpans"][0]["scopeSpans"][0]["spans"])
    console.print(f"[green]✓[/green] Exported {len(summaries)} trace(s), {span_count} span(s) to {path}")
    if endpoint:
        try:
            status = post_otlp(request, endpoint)
        except (RuntimeError, ValueError) as exc:
            err_console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc
        console.print(f"[green]✓[/green] Sent to {endpoint} (HTTP {status})")


TRACE_FORMATS = ("otel-json", "otlp-json")


def _archived_messages(summary: SessionSummary) -> list[RawMessage]:
    """Rebuild messages from the raw archive for per-tool spans ([] if absent)."""
    raw = store.load_raw_session(summary.session_id)
    if not raw:
        return []
    try:
        return get_provider(summary.provider or DEFAULT_PROVIDER).messages_from_raw(raw)
    except ValueError:
        return []

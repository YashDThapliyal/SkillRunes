"""OTel-style trace export helpers."""

from __future__ import annotations

from skillrunes.models import SessionSummary, TraceExport
from skillrunes.redact import redact_text
from skillrunes.utils import now_utc


def build_trace_exports(
    summaries: list[SessionSummary],
    provider: str = "unknown",
    redaction_level: str = "basic",
    include_skill_update: bool = True,
) -> list[TraceExport]:
    """Convert session summaries into redacted trace-like exports."""
    exports: list[TraceExport] = []
    for summary in summaries:
        status = _status(summary)
        goal = _safe(summary.goal, redaction_level)
        spans = [
            {
                "name": f"skillrunes.session {summary.session_id}",
                "kind": "internal",
                "attributes": {
                    "skillrunes.provider": provider,
                    "skillrunes.session_id": summary.session_id,
                    "skillrunes.status": status,
                    "skillrunes.message_count": summary.message_count,
                    "skillrunes.compaction_occurred": summary.compaction_occurred,
                    "gen_ai.usage.input_tokens": summary.token_count,
                    "skillrunes.duration_seconds": summary.duration_seconds,
                },
                "events": [
                    {"name": "goal", "attributes": {"skillrunes.goal": goal}},
                    {
                        "name": "observations",
                        "attributes": {
                            "skillrunes.what_worked.count": len(summary.what_worked),
                            "skillrunes.what_failed.count": len(summary.what_failed),
                            "skillrunes.patterns.count": len(summary.patterns),
                        },
                    },
                ],
            }
        ]
        if summary.compaction_occurred:
            spans[0]["events"].append({"name": "context.compaction", "attributes": {}})
        for failure in summary.what_failed:
            spans[0]["events"].append(
                {"name": "skillrunes.failure", "attributes": {"message": _safe(failure, redaction_level)}}
            )
        for pattern in summary.patterns:
            spans[0]["events"].append(
                {"name": "skillrunes.pattern", "attributes": {"message": _safe(pattern, redaction_level)}}
            )
        if include_skill_update:
            spans[0]["events"].append(
                {
                    "name": "skillrunes.skill_update",
                    "attributes": {
                        "skillrunes.skill_update.contributed": True,
                        "skillrunes.patterns.count": len(summary.patterns),
                    },
                }
            )

        exports.append(
            TraceExport(
                session_id=summary.session_id,
                provider=provider,
                spans=spans,
                redaction_report=summary.redaction_report,
                generated_at=now_utc(),
            )
        )
    return exports


def _status(summary: SessionSummary) -> str:
    if summary.what_worked and not summary.what_failed:
        return "clean"
    if summary.what_worked and summary.what_failed:
        return "partial"
    return "failed"


def _safe(value: str, level: str) -> str:
    redacted, _ = redact_text(value, level)
    return redacted

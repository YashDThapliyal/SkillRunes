"""OTLP/JSON trace export using the OpenTelemetry GenAI semantic conventions.

Each analysed session becomes one trace:

    invoke_agent <provider>            (root span, whole session)
      ├── execute_tool <tool name>     (one span per tool call)
      └── ...

Attribute names follow the GenAI conventions (development status as of
semconv 1.3x/1.4x): gen_ai.operation.name, gen_ai.agent.name,
gen_ai.conversation.id, gen_ai.tool.name, gen_ai.tool.call.id,
gen_ai.usage.input_tokens / output_tokens.  SkillRunes-specific measurements
use the skillrunes.* namespace.  Tool inputs and outputs are never exported —
only names, timing, and success/failure — so exports stay privacy-safe; the
session goal text is redacted before export.

Output is an ExportTraceServiceRequest JSON document accepted by OTLP/HTTP
collectors (POST /v1/traces) such as the OpenTelemetry Collector, Jaeger,
Arize Phoenix or Langfuse.  No I/O happens here except the optional POST.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from skillrunes import __version__
from skillrunes.models import RawMessage, SessionSummary
from skillrunes.redact import redact_text

SCOPE_NAME = "skillrunes"
SPAN_KIND_INTERNAL = 1
STATUS_OK = 1
STATUS_ERROR = 2
_POST_TIMEOUT = 30


def build_otlp_request(
    sessions: list[tuple[SessionSummary, list[RawMessage]]],
    *,
    service_name: str = "skillrunes",
    redaction_level: str = "basic",
) -> dict[str, Any]:
    """Build one ExportTraceServiceRequest covering every session."""
    spans = [
        span
        for summary, messages in sessions
        for span in session_spans(summary, messages, redaction_level)
    ]
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attrs({"service.name": service_name})},
                "scopeSpans": [
                    {"scope": {"name": SCOPE_NAME, "version": __version__}, "spans": spans}
                ],
            }
        ]
    }


def session_spans(
    summary: SessionSummary,
    messages: list[RawMessage],
    redaction_level: str = "basic",
) -> list[dict[str, Any]]:
    """Return the root agent span plus one execute_tool span per tool call."""
    trace_id = _hex_id(f"trace:{summary.session_id}", 32)
    root_id = _hex_id(f"root:{summary.session_id}", 16)
    start, end = _session_bounds(summary, messages)
    provider = summary.provider or "claude_code"
    signals = summary.signals

    root_attrs: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": provider,
        "gen_ai.conversation.id": summary.session_id,
        "gen_ai.usage.input_tokens": sum(m.usage.input_tokens for m in messages if m.usage),
        "gen_ai.usage.output_tokens": sum(m.usage.output_tokens for m in messages if m.usage),
        "skillrunes.goal": redact_text(summary.goal, redaction_level)[0],
        "skillrunes.message_count": summary.message_count,
        "skillrunes.compaction_occurred": summary.compaction_occurred,
    }
    if signals is not None:
        root_attrs |= {
            "skillrunes.tool_calls": signals.tool_calls,
            "skillrunes.tool_errors": signals.tool_errors,
            "skillrunes.retry_loops": signals.retry_loops,
            "skillrunes.user_corrections": signals.user_corrections,
            "skillrunes.interruptions": signals.interruptions,
        }
    has_errors = bool(signals and signals.tool_errors)
    root = _span(
        trace_id, root_id, "", f"invoke_agent {provider}", start, end, root_attrs,
        STATUS_ERROR if has_errors else STATUS_OK,
    )
    return [root, *_tool_spans(trace_id, root_id, messages, start)]


def post_otlp(request: dict[str, Any], endpoint: str) -> int:
    """POST an OTLP/JSON request to <endpoint>/v1/traces; return HTTP status."""
    url = endpoint.rstrip("/")
    if not url.endswith("/v1/traces"):
        url += "/v1/traces"
    if not url.startswith(("http://", "https://")):
        raise ValueError("OTLP endpoint must be an http(s) URL.")
    body = json.dumps(request).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_POST_TIMEOUT) as response:  # noqa: S310 - scheme checked
            return int(response.status)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OTLP collector rejected export: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach OTLP collector at {url}: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tool_spans(
    trace_id: str, parent_id: str, messages: list[RawMessage], fallback: datetime
) -> list[dict[str, Any]]:
    calls: dict[str, tuple[str, datetime]] = {}
    order: list[str] = []
    for msg in messages:
        for call in msg.tool_calls:
            calls[call.id] = (call.name, msg.timestamp or fallback)
            order.append(call.id)
    finished: dict[str, tuple[bool, datetime | None]] = {}
    for msg in messages:
        for result in msg.tool_results:
            finished[result.tool_use_id] = (result.is_error, msg.timestamp)

    spans: list[dict[str, Any]] = []
    for call_id in order:
        name, started = calls[call_id]
        is_error, ended = finished.get(call_id, (False, None))
        end = ended if ended and ended >= started else started
        spans.append(
            _span(
                trace_id,
                _hex_id(f"tool:{trace_id}:{call_id}", 16),
                parent_id,
                f"execute_tool {name}",
                started,
                end,
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": name,
                    "gen_ai.tool.call.id": call_id,
                },
                STATUS_ERROR if is_error else STATUS_OK,
            )
        )
    return spans


def _session_bounds(summary: SessionSummary, messages: list[RawMessage]) -> tuple[datetime, datetime]:
    stamps = [m.timestamp for m in messages if m.timestamp is not None]
    if stamps:
        return min(stamps), max(stamps)
    start = summary.started_at or summary.analyzed_at
    return start, start + timedelta(seconds=summary.duration_seconds)


def _span(
    trace_id: str,
    span_id: str,
    parent_id: str,
    name: str,
    start: datetime,
    end: datetime,
    attributes: dict[str, Any],
    status: int,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": SPAN_KIND_INTERNAL,
        "startTimeUnixNano": str(_nanos(start)),
        "endTimeUnixNano": str(_nanos(end)),
        "attributes": _attrs(attributes),
        "status": {"code": status},
    }
    if parent_id:
        span["parentSpanId"] = parent_id
    return span


def _attrs(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": _any_value(value)} for key, value in values.items()]


def _any_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _nanos(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _hex_id(seed: str, length: int) -> str:
    """Deterministic non-zero hex id so re-exports are idempotent."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]
    return digest if set(digest) != {"0"} else "1" + digest[1:]

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import skillrunes.store as store
from skillrunes.cli import app
from skillrunes.models import RawMessage, SessionSignals, SessionSummary, ToolCall, ToolResult
from skillrunes.otlp import build_otlp_request, post_otlp, session_spans

T0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 1, 12, 0, 5, tzinfo=timezone.utc)
SECRET = "sk-abcdefghijklmnopqrstuvwxyz123456"


def _summary() -> SessionSummary:
    return SessionSummary(
        session_id="sess-1",
        goal=f"rotate key OPENAI_API_KEY={SECRET}",
        analyzed_at=T1,
        provider="codex",
        signals=SessionSignals(tool_calls=2, tool_errors=1, retry_loops=0),
    )


def _messages() -> list[RawMessage]:
    return [
        RawMessage(type="assistant", role="assistant", timestamp=T0, tool_calls=[
            ToolCall(id="c1", name="shell", input={"command": f"echo {SECRET}"}),
            ToolCall(id="c2", name="apply_patch", input={}),
        ]),
        RawMessage(type="user", role="user", timestamp=T1, tool_results=[
            ToolResult(tool_use_id="c1", is_error=True, content=SECRET),
            ToolResult(tool_use_id="c2", is_error=False),
        ]),
    ]


def _attr(span: dict[str, Any], key: str) -> Any:
    value = next(a["value"] for a in span["attributes"] if a["key"] == key)
    return next(iter(value.values()))


@pytest.mark.unit
def test_session_spans_follow_genai_conventions() -> None:
    root, shell, patch = session_spans(_summary(), _messages())

    assert root["name"] == "invoke_agent codex"
    assert _attr(root, "gen_ai.operation.name") == "invoke_agent"
    assert _attr(root, "gen_ai.conversation.id") == "sess-1"
    assert _attr(root, "skillrunes.tool_errors") == "1"
    assert root["status"]["code"] == 2
    assert "parentSpanId" not in root
    assert shell["name"] == "execute_tool shell"
    assert shell["parentSpanId"] == root["spanId"]
    assert shell["traceId"] == root["traceId"] and len(root["traceId"]) == 32 and len(shell["spanId"]) == 16
    assert shell["status"]["code"] == 2 and patch["status"]["code"] == 1
    assert int(shell["endTimeUnixNano"]) - int(shell["startTimeUnixNano"]) == 5_000_000_000


@pytest.mark.unit
def test_export_never_contains_tool_io_or_secrets() -> None:
    request = build_otlp_request([(_summary(), _messages())])

    text = json.dumps(request)
    assert SECRET not in text
    assert "echo" not in text
    assert text.count('"name": "execute_tool ') == 2


@pytest.mark.unit
def test_ids_are_deterministic_and_session_without_messages_uses_summary_times() -> None:
    first = session_spans(_summary(), [])
    second = session_spans(_summary(), [])

    assert first == second
    assert len(first) == 1
    assert first[0]["startTimeUnixNano"] == str(int(T1.timestamp() * 1e9))


@pytest.mark.unit
def test_request_envelope() -> None:
    request = build_otlp_request([(_summary(), [])], service_name="svc")

    resource = request["resourceSpans"][0]
    assert resource["resource"]["attributes"][0] == {"key": "service.name", "value": {"stringValue": "svc"}}
    assert resource["scopeSpans"][0]["scope"]["name"] == "skillrunes"


class _Collector(BaseHTTPRequestHandler):
    received: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers["Content-Length"])
        _Collector.received.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def collector() -> str:
    _Collector.received = []
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.integration
def test_post_otlp_to_collector(collector: str) -> None:
    status = post_otlp(build_otlp_request([(_summary(), _messages())]), collector)

    assert status == 200
    assert _Collector.received[0]["path"] == "/v1/traces"
    assert "resourceSpans" in _Collector.received[0]["body"]


@pytest.mark.unit
def test_post_otlp_errors() -> None:
    with pytest.raises(ValueError, match="http"):
        post_otlp({}, "ftp://example.com")
    with pytest.raises(RuntimeError, match="Could not reach"):
        post_otlp({}, "http://127.0.0.1:9")


@pytest.mark.integration
def test_cli_otlp_export_uses_raw_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collector: str) -> None:
    monkeypatch.chdir(tmp_path)
    store.init_store()
    store.save_session_summary(_summary().model_copy(update={"provider": "claude_code"}))
    store.archive_raw_session("sess-1", [
        {"type": "assistant", "timestamp": "2026-11-01T12:00:00Z", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}},
        {"type": "user", "timestamp": "2026-11-01T12:00:02Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "x"}]}},
    ])

    result = CliRunner().invoke(app, ["export-traces", "--format", "otlp-json", "--endpoint", collector])

    assert result.exit_code == 0, result.output
    assert "2 span(s)" in result.output
    assert len(list((tmp_path / ".skillrunes" / "traces").glob("otlp-traces-*.json"))) == 1
    assert len(_Collector.received) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--format", "xml"], "--format must be one of"),
        (["--endpoint", "http://x"], "--endpoint requires"),
        (["--format", "otlp-json", "--redact-level", "off"], "requires basic or strict"),
    ],
)
def test_cli_export_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], message: str) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["export-traces", *args])

    assert result.exit_code == 1
    assert message in result.output


@pytest.mark.unit
def test_naive_times_duplicate_ids_and_nan_are_safe() -> None:
    naive = datetime(2026, 11, 1, 12, 0, 0)
    messages = [
        RawMessage(type="assistant", role="assistant", timestamp=naive, tool_calls=[
            ToolCall(id="dup", name="a"), ToolCall(id="dup", name="b"), ToolCall(id="", name="c"),
        ]),
    ]
    summary = _summary().model_copy(update={"duration_seconds": float("nan")})

    spans = session_spans(summary, messages)

    assert spans[0]["startTimeUnixNano"] == str(int(T0.timestamp() * 1e9))
    assert len({s["spanId"] for s in spans}) == 4
    json.dumps(build_otlp_request([(summary, messages)]), allow_nan=False)

"""OpenAI Codex CLI session transcript provider.

Codex writes one "rollout" JSONL file per session:

    ~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<session-uuid>.jsonl

Every line is an envelope ``{"timestamp", "type", "payload"}``.  The record
types SkillRunes maps onto its provider-agnostic RawMessage model are:

    session_meta                         session id + cwd (project matching)
    response_item / message              user / assistant text
    response_item / function_call        tool call, JSON-string arguments
    response_item / custom_tool_call     tool call, free-form string input
    response_item / *_call_output        tool result, matched by call_id
    event_msg / token_count              per-turn token usage
    event_msg / turn_aborted             user interruption

Everything else (reasoning, developer messages, world_state, rate limits) is
kept in raw_lines for archiving but not turned into messages.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rich.console import Console

from skillrunes.config import CODEX_SESSION_ROOTS
from skillrunes.ingest.base import TranscriptProvider
from skillrunes.models import (
    TOOL_RESULT_SNIPPET_CHARS,
    RawMessage,
    ToolCall,
    ToolResult,
    Usage,
)

console = Console(stderr=True)

_SESSION_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_NONZERO_EXIT_RE = re.compile(
    r"(\"exit_code\\*\"\s*:\s*-?[1-9]\d*|Exit code:?\s*-?[1-9]\d*|Process exited with code -?[1-9])"
)
_FAILURE_MARKERS = ("Script failed", '"status":"rejected"', "\\\"status\\\":\\\"rejected\\\"")

# Harness-injected context that Codex sends as role=user; not human-authored.
_INJECTED_USER_PREFIXES = (
    "<environment_context>",
    "<recommended_plugins>",
    "<user_instructions>",
    "<skills_instructions>",
    "# agents.md instructions",
)
_HUMAN_CONTENT_KIND = "user.text"
_INTERRUPT_TEXT = "[Request interrupted by user]"
_TOOL_OUTPUT_TYPES = frozenset(
    {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}
)


class CodexProvider(TranscriptProvider):
    """Session provider for OpenAI Codex CLI rollout transcripts."""

    name = "codex"
    display_name = "Codex"

    def __init__(self, roots: tuple[str, ...] | None = None) -> None:
        self._roots = roots or CODEX_SESSION_ROOTS

    def find_session_files(self, cwd: Path) -> list[Path]:
        """Return rollout files whose session_meta cwd equals cwd, oldest first."""
        cwd_str = str(cwd)
        matches: list[Path] = []
        # Dedupe by inode: on case-insensitive filesystems (macOS default)
        # ~/.codex and ~/.Codex are the same directory, and Path.resolve()
        # does not canonicalise case.
        seen: set[tuple[int, int]] = set()
        for root in self._roots:
            root_path = Path(root).expanduser()
            if not root_path.is_dir():
                continue
            for path in root_path.rglob("*.jsonl"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                identity = (stat.st_dev, stat.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                if _session_cwd(path) == cwd_str:
                    matches.append(path)
        return sorted(matches, key=lambda p: p.stat().st_mtime)

    def parse_session_file(self, path: Path) -> tuple[list[RawMessage], list[dict]]:
        """Parse a rollout file into (messages, raw_lines)."""
        raw_lines = _read_jsonl(path)
        session_id = self.session_id_from_path(path)
        parser = _RolloutParser(session_id)
        for record in raw_lines:
            parser.feed(record)
        return parser.messages, raw_lines

    def session_id_from_path(self, path: Path) -> str:
        """Return the session UUID embedded at the end of the rollout filename."""
        match = _SESSION_ID_RE.search(path.stem)
        return match.group(1) if match else path.stem

    def expected_location(self, cwd: Path) -> str:
        return ", ".join(self._roots)


class _RolloutParser:
    """Stateful single-pass converter from rollout records to RawMessages."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.cwd = ""
        self.messages: list[RawMessage] = []
        self._call_names: dict[str, str] = {}

    def feed(self, record: dict[str, Any]) -> None:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        record_type = record.get("type")
        timestamp = record.get("timestamp")
        if record_type == "session_meta":
            self.cwd = str(payload.get("cwd", ""))
            self.session_id = str(payload.get("id") or self.session_id)
        elif record_type == "turn_context":
            self.cwd = str(payload.get("cwd") or self.cwd)
        elif record_type == "response_item":
            self._feed_response_item(payload, timestamp)
        elif record_type == "event_msg":
            self._feed_event(payload, timestamp)

    def _feed_response_item(self, payload: dict[str, Any], timestamp: Any) -> None:
        item_type = payload.get("type")
        if item_type == "message":
            self._feed_message(payload, timestamp)
        elif item_type in {"function_call", "custom_tool_call", "local_shell_call"}:
            call = _tool_call(payload)
            self._call_names[call.id] = call.name
            self._append("assistant", timestamp, tool_calls=[call])
        elif item_type in _TOOL_OUTPUT_TYPES:
            output = _stringify(payload.get("output", ""))
            result = ToolResult(
                tool_use_id=str(payload.get("call_id", "")),
                is_error=_is_failed_output(output),
                content=output[:TOOL_RESULT_SNIPPET_CHARS],
            )
            self._append("user", timestamp, tool_results=[result])

    def _feed_message(self, payload: dict[str, Any], timestamp: Any) -> None:
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            return
        text = _message_text(payload.get("content", []))
        if not text:
            return
        if role == "user" and not _is_human_message(payload, text):
            return
        self._append(role, timestamp, content=text)

    def _feed_event(self, payload: dict[str, Any], timestamp: Any) -> None:
        event_type = payload.get("type")
        if event_type == "turn_aborted":
            self._append("user", timestamp, content=_INTERRUPT_TEXT)
        elif event_type == "token_count":
            usage = _usage_from_info(payload.get("info"))
            if usage is not None:
                self._attach_usage(usage)

    def _attach_usage(self, usage: Usage) -> None:
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if message.role == "assistant" and message.usage is None:
                self.messages[index] = message.model_copy(update={"usage": usage})
                return

    def _append(
        self,
        role: str,
        timestamp: Any,
        *,
        content: str = "",
        tool_calls: list[ToolCall] | None = None,
        tool_results: list[ToolResult] | None = None,
    ) -> None:
        self.messages.append(
            RawMessage(
                session_id=self.session_id,
                type=role,
                role=role,
                timestamp=timestamp,
                content=content,
                tool_calls=tool_calls or [],
                tool_results=tool_results or [],
                cwd=self.cwd,
            )
        )


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    console.print(
                        f"[yellow]Warning:[/yellow] skipping malformed JSON on "
                        f"{path.name}:{lineno} — {exc}"
                    )
                    continue
                if isinstance(data, dict):
                    records.append(data)
    except OSError as exc:
        console.print(f"[red]Error:[/red] could not read {path} — {exc}")
    return records


def _session_cwd(path: Path) -> str:
    """Return the cwd from the first session_meta record, or ''."""
    try:
        with path.open(encoding="utf-8") as fh:
            for _, line in zip(range(5), fh):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "session_meta":
                    return str((data.get("payload") or {}).get("cwd", ""))
    except OSError:
        return ""
    return ""


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") in {"input_text", "output_text", "text"}
    ]
    return "\n".join(part for part in parts if part).strip()


def _is_human_message(payload: dict[str, Any], text: str) -> bool:
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, dict) and metadata.get("content_item_kinds"):
        kinds = metadata["content_item_kinds"]
        return all(kind == _HUMAN_CONTENT_KIND for kind in kinds)
    lowered = text.lstrip().lower()
    return not any(lowered.startswith(prefix) for prefix in _INJECTED_USER_PREFIXES)


def _tool_call(payload: dict[str, Any]) -> ToolCall:
    call_id = str(payload.get("call_id") or payload.get("id") or "")
    name = str(payload.get("name") or payload.get("type", "tool"))
    if "arguments" in payload:
        arguments = _parse_arguments(payload.get("arguments"))
    elif "action" in payload:
        arguments = payload["action"] if isinstance(payload["action"], dict) else {}
    else:
        arguments = {"input": _stringify(payload.get("input", ""))}
    return ToolCall(id=call_id, name=name, input=arguments)


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"input": arguments}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    return {}


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text = _message_text(value)
        if text:
            return text
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _is_failed_output(output: str) -> bool:
    if any(marker in output for marker in _FAILURE_MARKERS):
        return True
    return bool(_NONZERO_EXIT_RE.search(output))


def _usage_from_info(info: Any) -> Usage | None:
    if not isinstance(info, dict):
        return None
    last = info.get("last_token_usage")
    if not isinstance(last, dict):
        return None
    cached = int(last.get("cached_input_tokens", 0) or 0)
    total_input = int(last.get("input_tokens", 0) or 0)
    return Usage(
        input_tokens=max(total_input - cached, 0),
        output_tokens=int(last.get("output_tokens", 0) or 0),
        cache_read_input_tokens=cached,
    )

"""Privacy redaction helpers for transcripts and generated artifacts."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from skillrunes.models import RawMessage, RedactionReport, ToolCall
from skillrunes.utils import now_utc

REDACTION_LEVELS = ("basic", "strict", "off")

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    (
        "env_secret",
        re.compile(
            r"(?im)^([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*\s*=\s*)([^\s#]+)"
        ),
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("auth_header", re.compile(r"(?i)(Authorization\s*:\s*)(?:Bearer\s+)?([A-Za-z0-9._~+/=-]{16,})")),
    ("credential_url", re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@")),
]

_STRICT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("home_path", re.compile(r"/Users/[^/\s]+|/home/[^/\s]+")),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


def validate_redaction_level(level: str) -> str:
    """Return a valid redaction level or raise ValueError."""
    if level not in REDACTION_LEVELS:
        raise ValueError(
            f"Unknown redaction level {level!r}. Expected: {', '.join(REDACTION_LEVELS)}."
        )
    return level


def redact_text(text: str, level: str, project_root: Path | None = None) -> tuple[str, dict[str, int]]:
    """Redact sensitive text and return (redacted_text, detector_counts)."""
    validate_redaction_level(level)
    if level == "off" or not text:
        return text, {}

    counts: dict[str, int] = {}

    def repl(name: str, replacement: str):
        def _inner(match: re.Match[str]) -> str:
            counts[name] = counts.get(name, 0) + 1
            if name in {"env_secret", "auth_header"} and match.lastindex and match.lastindex >= 2:
                return match.group(1) + replacement
            if name == "credential_url":
                return match.group(1) + "redacted@"
            return replacement

        return _inner

    redacted = text
    for name, pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(repl(name, f"[REDACTED:{name}]"), redacted)

    if level == "strict":
        redacted = _redact_url_query_strings(redacted, counts)
        for name, pattern in _STRICT_PATTERNS:
            redacted = pattern.sub(repl(name, f"[REDACTED:{name}]"), redacted)
        if project_root is not None:
            redacted = _redact_external_paths(redacted, project_root, counts)

    return redacted, counts


def redact_messages_and_raw(
    messages: list[RawMessage],
    raw_lines: list[dict],
    level: str,
    project_root: Path,
) -> tuple[list[RawMessage], list[dict], RedactionReport]:
    """Redact parsed messages and raw transcript dictionaries."""
    validate_redaction_level(level)
    if level == "off":
        return messages, raw_lines, RedactionReport(
            level=level, detector_counts={}, redacted_fields=[], timestamp=now_utc()
        )

    detector_counts: dict[str, int] = {}
    redacted_fields: set[str] = set()

    def apply(value: str, field: str) -> str:
        redacted, counts = redact_text(value, level, project_root)
        if redacted != value:
            redacted_fields.add(field)
        _merge_counts(detector_counts, counts)
        return redacted

    safe_messages: list[RawMessage] = []
    for msg in messages:
        safe_tool_calls = [
            ToolCall(
                id=tool.id,
                name=tool.name,
                input=_redact_obj(tool.input, level, project_root, detector_counts, f"tool:{tool.name}"),
            )
            for tool in msg.tool_calls
        ]
        safe_tool_results = [
            result.model_copy(update={"content": apply(result.content, "tool_result.content")})
            for result in msg.tool_results
        ]
        safe_messages.append(
            msg.model_copy(
                update={
                    "content": apply(msg.content, "message.content"),
                    "cwd": apply(msg.cwd, "message.cwd"),
                    "tool_calls": safe_tool_calls,
                    "tool_results": safe_tool_results,
                }
            )
        )

    safe_raw = _redact_obj(deepcopy(raw_lines), level, project_root, detector_counts, "raw")

    return safe_messages, safe_raw, RedactionReport(
        level=level,
        detector_counts=detector_counts,
        redacted_fields=sorted(redacted_fields),
        timestamp=now_utc(),
    )


def scan_text(text: str, level: str = "strict", project_root: Path | None = None) -> dict[str, int]:
    """Return detector counts without exposing matched values."""
    _, counts = redact_text(text, level, project_root)
    return counts


def scan_paths(paths: list[Path], project_root: Path, level: str = "basic") -> RedactionReport:
    """Scan files for likely secrets and return an aggregate report."""
    validate_redaction_level(level)
    totals: dict[str, int] = {}
    fields: list[str] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        counts = scan_text(text, level, project_root)
        if counts:
            fields.append(str(path))
            _merge_counts(totals, counts)
    return RedactionReport(
        level=level,
        detector_counts=totals,
        redacted_fields=fields,
        timestamp=now_utc(),
    )


def _redact_obj(
    obj: Any,
    level: str,
    project_root: Path,
    detector_counts: dict[str, int],
    field: str,
) -> Any:
    if isinstance(obj, str):
        redacted, counts = redact_text(obj, level, project_root)
        _merge_counts(detector_counts, counts)
        return redacted
    if isinstance(obj, list):
        return [_redact_obj(item, level, project_root, detector_counts, field) for item in obj]
    if isinstance(obj, dict):
        return {
            key: _redact_obj(value, level, project_root, detector_counts, f"{field}.{key}")
            for key, value in obj.items()
        }
    return obj


def _merge_counts(total: dict[str, int], counts: dict[str, int]) -> None:
    for key, value in counts.items():
        total[key] = total.get(key, 0) + value


def _redact_url_query_strings(text: str, counts: dict[str, int]) -> str:
    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        if not parts.query:
            return url
        query = parse_qsl(parts.query, keep_blank_values=True)
        if query and all(value == "[REDACTED:url_query]" for _, value in query):
            return url
        redacted_query = urlencode([(key, "[REDACTED:url_query]") for key, _ in query])
        counts["url_query"] = counts.get("url_query", 0) + len(query)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, parts.fragment))

    return re.sub(r"\b[a-z][a-z0-9+.-]*://[^\s)>\]]+", replace_url, text)


def _redact_external_paths(text: str, project_root: Path, counts: dict[str, int]) -> str:
    root = str(project_root.resolve())
    path_re = re.compile(r"(?<![\w.-])/(?:[A-Za-z0-9._ -]+/)+[A-Za-z0-9._ -]+")

    def replace_path(match: re.Match[str]) -> str:
        path = match.group(0)
        if path.startswith(root):
            return path
        counts["external_path"] = counts.get("external_path", 0) + 1
        return "[REDACTED:external_path]"

    return path_re.sub(replace_path, text)


def dumps_redacted_json(data: Any) -> str:
    """Stable JSON helper for debugging/tests."""
    return json.dumps(data, indent=2, default=str, sort_keys=True)

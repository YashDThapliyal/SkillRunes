"""Pydantic data models for SkillRunes.

All data structures used across the application are defined here before being
referenced elsewhere.  Every model uses ConfigDict(extra="ignore") so that
schema drift in transcript files (new fields added by future provider versions)
does not crash the parser.
"""

from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# JSONL parsing models
# ---------------------------------------------------------------------------

class ToolCall(BaseModel):
    """A single tool invocation extracted from an assistant message."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str
    input: dict[str, Any] = {}


class Usage(BaseModel):
    """Token usage reported on an assistant message."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class RawMessage(BaseModel):
    """One parsed line from a coding-agent JSONL session transcript.

    The classmethod `from_jsonl_line` normalises the polymorphic JSONL format
    into this unified representation.  Lines that carry no semantic content
    (hook attachments, file-history snapshots, permission-mode records, etc.)
    return None so callers can skip them without special-casing each type.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str = ""
    uuid: str = ""
    parent_uuid: str | None = ""
    type: str
    role: str | None = None
    timestamp: datetime | None = None
    content: str = ""
    tool_calls: list[ToolCall] = []
    cwd: str = ""
    usage: Usage | None = None

    # Line types that carry semantic conversation content worth analysing.
    # ClassVar keeps Pydantic from treating this as a model field or private attr.
    MEANINGFUL_TYPES: ClassVar[frozenset[str]] = frozenset({"user", "assistant"})

    @classmethod
    def from_jsonl_line(cls, data: dict[str, Any]) -> "RawMessage | None":
        """Parse a raw dict from one JSONL line into a RawMessage.

        Returns None for line types that contain no conversation content
        (noise lines such as attachments, snapshots, permission records).
        """
        line_type = data.get("type", "")
        if line_type not in cls.MEANINGFUL_TYPES:
            return None

        message = data.get("message", {})
        role = message.get("role", line_type)
        raw_content = message.get("content", "")

        # Normalise content: strings stay as-is; block lists are flattened to
        # plain text with tool_use blocks extracted separately.
        content_text = ""
        tool_calls: list[ToolCall] = []

        if isinstance(raw_content, str):
            content_text = raw_content
        elif isinstance(raw_content, list):
            text_parts: list[str] = []
            for block in raw_content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                        )
                    )
                # thinking and tool_result blocks are skipped intentionally
            content_text = "\n".join(text_parts)

        usage_data = message.get("usage")
        usage = Usage(**usage_data) if usage_data else None

        return cls(
            session_id=data.get("sessionId", ""),
            uuid=data.get("uuid", ""),
            parent_uuid=data.get("parentUuid", ""),
            type=line_type,
            role=role,
            timestamp=data.get("timestamp"),
            content=content_text,
            tool_calls=tool_calls,
            cwd=data.get("cwd", ""),
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Extraction output models
# ---------------------------------------------------------------------------

class SessionSummary(BaseModel):
    """Extracted analysis of one coding-agent session.

    Produced by extract.py via the model client and persisted to
    .skillrunes/sessions/<session_id>.json.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    goal: str
    approaches_tried: list[str] = []
    what_worked: list[str] = []
    what_failed: list[str] = []
    patterns: list[str] = []
    token_count: int = 0
    duration_seconds: float = 0.0
    compaction_occurred: bool = False
    analyzed_at: datetime
    message_count: int = 0


# ---------------------------------------------------------------------------
# Skill versioning models
# ---------------------------------------------------------------------------

class SkillVersion(BaseModel):
    """One archived version of the SKILL.md file.

    Stored in .skillrunes/skills/v{version}.md; version numbers are
    monotonically increasing integers starting at 1.
    """

    model_config = ConfigDict(extra="ignore")

    version: int
    timestamp: datetime
    content: str
    change_summary: str = ""


# ---------------------------------------------------------------------------
# Aggregated metrics models
# ---------------------------------------------------------------------------

class TimePoint(BaseModel):
    """A single (date, numeric value) data point for time-series charts."""

    model_config = ConfigDict(extra="ignore")

    date: datetime
    value: float


class PatternCount(BaseModel):
    """A recurring pattern paired with how many sessions it appeared in."""

    model_config = ConfigDict(extra="ignore")

    pattern: str
    count: int


class ProjectMetrics(BaseModel):
    """Aggregated metrics across all analysed sessions for this project.

    Computed by utils.compute_metrics() and persisted to
    .skillrunes/metrics.json for the dashboard to read.
    """

    model_config = ConfigDict(extra="ignore")

    sessions_analyzed: int = 0
    success_rate_series: list[TimePoint] = []
    token_series: list[TimePoint] = []
    top_failure_patterns: list[PatternCount] = []

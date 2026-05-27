"""Session extraction layer for SkillRunes.

Responsible for:
- Analysing a parsed session transcript and producing a SessionSummary
- Managing large sessions by splitting into chunks and reducing with deduplication
- Defining prompt constants and the tool schema as module-level names

All model calls go through client.ModelClient — never instantiate SDK clients
here directly.  No file I/O is performed here.

Numeric fields (token_count, duration_seconds, message_count, compaction_occurred)
are computed deterministically from the messages themselves, never asked of the model.
The model is only asked for the five semantic fields: goal, approaches_tried,
what_worked, what_failed, patterns.
"""

import json

from anthropic.types import ToolParam

from skillrunes.client import ModelClient, get_client
from skillrunes.config import MAX_MESSAGES_PER_CHUNK
from skillrunes.models import RawMessage, SessionSummary
from skillrunes.utils import now_utc

# ---------------------------------------------------------------------------
# Tool schema — forces the model to return structured output via tool_use.
# Both the per-chunk extraction and the reduction merge call use this schema.
# ---------------------------------------------------------------------------

EXTRACTION_TOOL_SCHEMA: ToolParam = {
    "name": "extract_session_summary",
    "description": "Extract structured learnings from a coding-agent session transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "The primary task the user was trying to accomplish in this session."
                ),
            },
            "approaches_tried": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Distinct strategies or approaches attempted, one per item."
                ),
            },
            "what_worked": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Things that succeeded or moved the task forward.",
            },
            "what_failed": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Things that failed, caused errors, or were abandoned and why."
                ),
            },
            "patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Reusable insights an AI assistant should remember for future "
                    "sessions. Must be general principles, not session-specific facts."
                ),
            },
        },
        "required": [
            "goal",
            "approaches_tried",
            "what_worked",
            "what_failed",
            "patterns",
        ],
    },
}

# ---------------------------------------------------------------------------
# Prompt constants — edit these to iterate on extraction quality
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """\
You are analysing a coding-agent session transcript to extract structured learnings \
for an AI assistant improvement system.

The transcript is a sequence of [USER] and [ASSISTANT] turns. \
Focus on what the user was trying to accomplish and how the assistant responded.

Extract:
- goal: The single primary task the user was working on (one sentence).
- approaches_tried: Each distinct strategy or method attempted (one per list item, \
specific and concrete).
- what_worked: Specific things that succeeded — tool calls that returned correct \
results, code that compiled, tests that passed, user confirmations of success.
- what_failed: Specific failures — errors thrown, incorrect outputs, abandoned \
approaches, explicit user corrections. Include the reason when visible.
- patterns: Reusable insights an AI assistant should remember for future sessions. \
These must be general principles, not session-specific facts. \
Examples of good patterns: "Run the linter before declaring a refactor complete", \
"Ask for clarification when the directory structure is ambiguous before creating files".

Be specific and concrete. Avoid vague generalities like "improved the code" or \
"there were some errors". Each list item should be a single observation.

Ignore conversational filler from the assistant such as acknowledgements, \
restatements of the task, or offer-to-help turns. Focus only on turns where \
actual tool calls were made or substantive code/analysis was produced.\
"""

# Chunk data is injected as a user-turn JSON array, not into this string,
# so the system prompt stays stable across calls and avoids f-string escaping.
REDUCTION_PROMPT = """\
You are merging {n} partial analyses of the same coding-agent session into one \
unified summary. Each partial analysis covers a non-overlapping chunk of the \
full transcript, so the same event or pattern may appear in multiple chunks.

Your job is to produce a single deduplicated summary using the \
extract_session_summary tool.

Strict deduplication rules — violating these produces a useless summary:
1. GOAL: Synthesise one goal that covers the whole session, not just the last chunk.
2. APPROACHES_TRIED: If the same approach appears in multiple chunks (even with \
different wording), include it ONCE using the most specific/detailed wording.
3. WHAT_WORKED: Same rule — union across chunks, deduplicated. If "tests passed \
after fixing the import" appears twice, include it once.
4. WHAT_FAILED: Same rule — union across chunks, deduplicated. Do NOT list the \
same failure multiple times just because it recurred across chunks. \
If "ModuleNotFoundError on anthropic import" appears in chunks 1 and 2, \
include it once.
5. PATTERNS: Same rule — if "validate inputs before API calls" appears in two \
chunks, include it once with the most complete wording.\
"""

# Maximum characters kept per message turn in the rendered transcript.
# Tool output and assistant prose can be very long; capping prevents token blowout
# while keeping the semantically important head of each turn.
_MAX_TURN_CHARS = 2000


# ---------------------------------------------------------------------------
# Transcript rendering
# ---------------------------------------------------------------------------

def render_transcript(messages: list[RawMessage]) -> str:
    """Flatten messages into a compact role-tagged text block for the API.

    Only user/assistant messages are included (noise lines are already filtered
    by ingest.parse_session_file).  Turns with no text content (tool-only turns
    with empty content) are skipped.  Each turn is capped at _MAX_TURN_CHARS to
    prevent token blowout from large tool outputs.
    """
    parts: list[str] = []
    for msg in messages:
        text = msg.content.strip()
        if not text:
            continue
        if len(text) > _MAX_TURN_CHARS:
            text = text[:_MAX_TURN_CHARS] + " [truncated]"
        role_tag = "[USER]" if msg.role == "user" else "[ASSISTANT]"
        parts.append(f"{role_tag}\n{text}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Deterministic numeric field computation
# ---------------------------------------------------------------------------

def _compute_numeric_fields(
    messages: list[RawMessage],
    raw_lines: list[dict],
) -> tuple[int, float, int, bool]:
    """Compute token_count, duration_seconds, message_count, compaction_occurred.

    All four are derived from the data — never asked of the model.

    token_count  : sum of input_tokens + output_tokens across all assistant messages
    duration_secs: elapsed seconds between first and last message timestamp
    message_count: total user + assistant messages
    compaction   : True if any system line has subtype containing "compact"
    """
    token_count = sum(
        (m.usage.input_tokens + m.usage.output_tokens)
        for m in messages
        if m.usage
    )

    timestamps = [m.timestamp for m in messages if m.timestamp is not None]
    if len(timestamps) >= 2:
        duration_seconds = (max(timestamps) - min(timestamps)).total_seconds()
    else:
        duration_seconds = 0.0

    message_count = len(messages)

    compaction_occurred = any(
        "compact" in str(line.get("subtype", "")).lower()
        for line in raw_lines
        if line.get("type") == "system"
    )

    return token_count, duration_seconds, message_count, compaction_occurred


# ---------------------------------------------------------------------------
# Per-chunk extraction and reduction
# ---------------------------------------------------------------------------

def _extract_chunk(client: ModelClient, chunk: list[RawMessage]) -> dict:
    """Analyse one chunk of messages and return the raw structured-output dict."""
    transcript = render_transcript(chunk)
    if not transcript.strip():
        return {
            "goal": "No substantive content in this chunk.",
            "approaches_tried": [],
            "what_worked": [],
            "what_failed": [],
            "patterns": [],
        }
    return client.extract_structured(ANALYSIS_PROMPT, transcript, EXTRACTION_TOOL_SCHEMA)


def _reduce_chunks(client: ModelClient, chunk_results: list[dict]) -> dict:
    """Merge N chunk dicts into one via a deduplicating reduction call.

    Chunk data is passed as a user-turn JSON array so the system prompt
    (REDUCTION_PROMPT) stays stable across calls and avoids f-string escaping.
    """
    n = len(chunk_results)
    system_prompt = REDUCTION_PROMPT.format(n=n)
    user_content = (
        f"Here are the {n} partial analyses to merge:\n\n"
        + json.dumps(chunk_results, indent=2)
    )
    return client.extract_structured(system_prompt, user_content, EXTRACTION_TOOL_SCHEMA)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_session(
    messages: list[RawMessage],
    session_id: str,
    raw_lines: list[dict] | None = None,
) -> SessionSummary:
    """Extract a SessionSummary from an agent session's messages via the model client.

    Numeric fields are computed deterministically:
      token_count        — summed from message.usage fields
      duration_seconds   — last timestamp minus first timestamp
      message_count      — len(messages)
      compaction_occurred — any system line with subtype containing "compact"

    For sessions with more than MAX_MESSAGES_PER_CHUNK messages, the transcript
    is split into equal-sized chunks, each analysed independently, then merged
    via a single reduction call (REDUCTION_PROMPT) that explicitly deduplicates
    patterns, failures, and all list fields — same item in multiple chunks
    appears exactly once in the final SessionSummary.

    raw_lines is used only for compaction detection; pass [] or None if unavailable.
    """
    client = get_client()
    _raw = raw_lines or []

    token_count, duration_seconds, message_count, compaction_occurred = (
        _compute_numeric_fields(messages, _raw)
    )

    if len(messages) <= MAX_MESSAGES_PER_CHUNK:
        result = _extract_chunk(client, messages)
    else:
        # Split into non-overlapping chunks, analyse each, then deduplicate-merge
        chunks = [
            messages[i : i + MAX_MESSAGES_PER_CHUNK]
            for i in range(0, len(messages), MAX_MESSAGES_PER_CHUNK)
        ]
        chunk_results = [_extract_chunk(client, chunk) for chunk in chunks]
        result = chunk_results[0] if len(chunk_results) == 1 else _reduce_chunks(client, chunk_results)

    return SessionSummary(
        session_id=session_id,
        goal=result.get("goal", ""),
        approaches_tried=result.get("approaches_tried", []),
        what_worked=result.get("what_worked", []),
        what_failed=result.get("what_failed", []),
        patterns=result.get("patterns", []),
        token_count=token_count,
        duration_seconds=duration_seconds,
        compaction_occurred=compaction_occurred,
        analyzed_at=now_utc(),
        message_count=message_count,
    )

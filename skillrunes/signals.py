"""Deterministic behaviour signals computed from parsed transcripts.

No model calls happen here.  These detectors give SkillRunes ground-truth
measurements (tool error rate, retry loops, user corrections, interruptions)
that are independent of the LLM extraction step, so skill versions can be
judged by what the agent actually did rather than by what a model says it did.

Definitions:
  tool_errors       tool results flagged ``is_error`` by the agent runtime
  retry_loops       an identical tool call (same name and arguments) issued
                    again immediately after that exact call failed, with no
                    other tool call in between (fail → edit → rerun is normal
                    iteration, not a retry)
  interruptions     the user stopped the agent mid-turn
  user_corrections  a user turn that pushes back on the agent ("no, ...",
                    "that's wrong", "revert that", ...)
"""

from __future__ import annotations

import json
import re
from collections import Counter

from skillrunes.models import RawMessage, SessionSignals

# Text the runtime injects into user turns; these are not human-authored.
_SYSTEM_PREFIXES = (
    "<command-",
    "<local-command",
    "<system-reminder>",
    "<task-notification>",
    "caveat:",
    "this session is being continued",
    "stop hook feedback",
    "[image",
    "base directory for this skill",
)

_INTERRUPT_MARKERS = (
    "[request interrupted by user",
    "<turn_aborted>",
)

_CORRECTION_START = re.compile(
    r"^\s*(no+(?![,\s]+(problem|worries|need|rush|go ahead|thanks|that'?s fine|it'?s fine))|"
    r"nope|wrong|stop|undo|revert|that'?s not|that is not|"
    r"not what|why did you|you (didn'?t|forgot|broke|missed))\b",
    re.IGNORECASE,
)
_CORRECTION_ANYWHERE = re.compile(
    r"\b(that'?s (wrong|incorrect|not right)|not what i (asked|wanted|meant)|"
    r"i (said|told you|asked you)|you (broke|forgot|ignored)|"
    r"revert (that|this|it)|undo (that|this|it)|try again)\b",
    re.IGNORECASE,
)


def compute_signals(messages: list[RawMessage]) -> SessionSignals:
    """Compute behaviour signals for one session's messages."""
    call_names: dict[str, str] = {}
    call_keys: dict[str, str] = {}
    ordered_call_ids: list[str] = []
    for msg in messages:
        for call in msg.tool_calls:
            call_names[call.id] = call.name
            call_keys[call.id] = _call_key(call.name, call.input)
            ordered_call_ids.append(call.id)

    errored_ids = {
        result.tool_use_id
        for msg in messages
        for result in msg.tool_results
        if result.is_error
    }

    errors_by_tool = Counter(
        call_names.get(call_id, "unknown") for call_id in errored_ids
    )

    user_texts = [_human_text(msg) for msg in messages if msg.role == "user"]
    user_texts = [text for text in user_texts if text]
    interruptions = sum(1 for text in user_texts if _is_interrupt(text))
    # The opening request is an instruction, never a correction of prior work.
    corrections = sum(
        1
        for text in user_texts[1:]
        if not _is_interrupt(text) and is_correction(text)
    )

    return SessionSignals(
        tool_calls=len(ordered_call_ids),
        tool_errors=len(errored_ids),
        retry_loops=_count_retries(ordered_call_ids, call_keys, errored_ids),
        user_corrections=corrections,
        interruptions=interruptions,
        user_turns=sum(1 for text in user_texts if not _is_interrupt(text)),
        errors_by_tool=dict(errors_by_tool),
    )


def is_correction(text: str) -> bool:
    """Return True if a human user turn reads as pushback on the agent."""
    head = text.strip()[:400]
    return bool(_CORRECTION_START.search(head) or _CORRECTION_ANYWHERE.search(head))


def _count_retries(
    ordered_call_ids: list[str],
    call_keys: dict[str, str],
    errored_ids: set[str],
) -> int:
    retries = 0
    previous_key = ""
    previous_failed = False
    for call_id in ordered_call_ids:
        key = call_keys[call_id]
        if previous_failed and key == previous_key:
            retries += 1
        previous_key, previous_failed = key, call_id in errored_ids
    return retries


def _call_key(name: str, arguments: dict) -> str:
    try:
        encoded = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        encoded = repr(arguments)
    return f"{name}:{encoded}"


def _human_text(msg: RawMessage) -> str:
    text = msg.content.strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in _SYSTEM_PREFIXES):
        return ""
    return text


def _is_interrupt(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _INTERRUPT_MARKERS)

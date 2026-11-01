"""Skill file synthesis layer for SkillRunes.

Responsible for:
- Taking all session summaries and the current SKILL.md content
- Calling the model client to produce an improved SKILL.md
- Returning both the new content and a short change summary for versioning

All model calls go through client.ModelClient — never instantiate SDK clients
here directly.  No file I/O is performed here.
"""

import json

from anthropic.types import ToolParam

from skillrunes.client import get_client
from skillrunes.models import RuleDelta, SessionSummary

# ---------------------------------------------------------------------------
# Prompt constant — edit this to iterate on synthesis quality
# ---------------------------------------------------------------------------

SYNTHESIS_PROMPT = """\
You are improving a SKILL.md file that captures reusable patterns and learnings \
for an AI coding assistant working on a software project.

SKILL.md is read at the start of every session to prime the assistant. \
Its purpose is to make the assistant progressively better at this specific project \
by encoding hard-won lessons from past sessions.

Your task:
1. PRESERVE all guidance that is still relevant and working — do not remove anything \
unless the new sessions show it is outdated or incorrect.
2. ADD new patterns, insights, and failure modes discovered in the new sessions. \
Only add items that are specific and actionable, not vague generalities.
3. REMOVE guidance only if the new sessions demonstrate it no longer applies.
4. CONSOLIDATE duplicate or overlapping guidance into single clear entries.
5. Keep entries concrete. Bad: "be careful with errors". \
Good: "always run pytest after editing any file in skillrunes/models.py — \
downstream models.py changes break ingest, store, and extract silently".

Format the output as a Markdown document with clear sections. \
Use ## headings for sections (e.g. ## Patterns, ## Common Failures, ## Project Context).

End the document with a ## What Changed section (2–4 sentences) summarising \
what was added, removed, or updated compared to the previous version. \
If this is the first version, describe what the file covers.

Output only the raw Markdown content — no code fences, no preamble.\
"""


def _build_user_message(
    summaries: list[SessionSummary],
    current_skill: str | None,
    memory_seed: str = "",
) -> str:
    """Build the user-turn message that provides context to the synthesis call."""
    parts: list[str] = []

    if memory_seed.strip():
        parts.append(memory_seed.strip())

    if current_skill:
        parts.append("## Current SKILL.md\n\n" + current_skill)
    else:
        parts.append(
            "## Current SKILL.md\n\n"
            "(No existing SKILL.md — create one from scratch based on the sessions below.)"
        )

    parts.append(_summaries_block(summaries))

    return "\n\n---\n\n".join(parts)


def _summaries_block(summaries: list[SessionSummary]) -> str:
    """Render session summaries (plus measured signals) as a JSON section."""
    summary_dicts = [
        {
            "session_id": s.session_id,
            "goal": s.goal,
            "approaches_tried": s.approaches_tried,
            "what_worked": s.what_worked,
            "what_failed": s.what_failed,
            "patterns": s.patterns,
            "token_count": s.token_count,
            "duration_seconds": round(s.duration_seconds, 1),
            "measured": _signals_dict(s),
        }
        for s in summaries
    ]
    return (
        f"## New session summaries ({len(summaries)} session(s))\n\n"
        + json.dumps(summary_dicts, indent=2)
    )


def _signals_dict(summary: SessionSummary) -> dict[str, object]:
    if summary.signals is None:
        return {}
    sig = summary.signals
    return {
        "tool_errors": sig.tool_errors,
        "retry_loops": sig.retry_loops,
        "user_corrections": sig.user_corrections,
        "interruptions": sig.interruptions,
        "errors_by_tool": sig.errors_by_tool,
    }


def _extract_change_summary(skill_content: str) -> str:
    """Pull the ## What Changed section out of the generated skill content.

    Returns the section text as a short string for SkillVersion.change_summary.
    Falls back to a generic message if the section is absent (model didn't
    follow the prompt instruction).
    """
    marker = "## What Changed"
    idx = skill_content.find(marker)
    if idx == -1:
        return "Skill file updated."
    # Grab everything after the marker heading up to the next ## heading or EOF
    after = skill_content[idx + len(marker):].strip()
    next_heading = after.find("\n##")
    if next_heading != -1:
        after = after[:next_heading].strip()
    # Collapse to one paragraph for storage
    return " ".join(after.split())


def generate_skill_file(
    summaries: list[SessionSummary],
    current_skill: str | None,
    memory_seed: str = "",
    billing: str | None = None,
) -> tuple[str, str]:
    """Generate an improved SKILL.md from session summaries and the current version.

    If current_skill is None, creates a SKILL.md from scratch based purely on
    the session summaries.  Otherwise, improves the existing content by
    preserving working guidance, adding new patterns, and removing outdated ones.

    Uses free-form text generation (not tool_use) because the output is Markdown
    prose, not a structured data object.

    Returns:
        (new_skill_content, change_summary) where change_summary is extracted
        from the ## What Changed section of the generated content and stored in
        SkillVersion.change_summary for the dashboard diff view.
    """
    client = get_client(billing)
    user_message = _build_user_message(summaries, current_skill, memory_seed)
    new_content = client.generate_text(SYNTHESIS_PROMPT, user_message)
    change_summary = _extract_change_summary(new_content)
    return new_content, change_summary


# ---------------------------------------------------------------------------
# Delta mode (ACE-style): propose rule edits instead of rewriting the file
# ---------------------------------------------------------------------------

DELTA_PROMPT = """\
You curate a rule-level memory for an AI coding assistant working on one \
software project. Rules are injected into every future session, so each one \
costs context: keep only concrete, project-specific, actionable guidance.

You receive the current rules (with ids and helpful/harmful vote counts) and \
summaries of new sessions. Propose a SMALL set of edit operations:

- add:     a new rule the sessions show is needed (section + text + session_ids).
           Prefer conventions the user or a reviewer had to correct, and \
failures that recurred. Do not add generic advice ("write tests", "be careful").
- update:  sharpen an existing rule's wording (rule_id + text).
- helpful: an existing rule that the sessions show was followed and paid off.
- harmful: an existing rule that misled the agent or is contradicted by the sessions.
- remove:  a rule that is clearly obsolete or wrong. Use sparingly.

Never restate the whole rulebook. Return an empty list when nothing should change. \
Cite the session_ids that justify each operation.\
"""

DELTA_TOOL_SCHEMA: ToolParam = {
    "name": "propose_rule_deltas",
    "description": "Propose incremental edits to the project rulebook.",
    "input_schema": {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["add", "update", "remove", "helpful", "harmful"]},
                        "rule_id": {"type": "string"},
                        "section": {"type": "string"},
                        "text": {"type": "string"},
                        "session_ids": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                    "required": ["op"],
                },
            }
        },
        "required": ["operations"],
    },
}


def propose_rule_deltas(
    summaries: list[SessionSummary],
    rules_listing: str,
    billing: str | None = None,
) -> list[RuleDelta]:
    """Ask the model for incremental rulebook edits justified by new sessions."""
    client = get_client(billing)
    user_message = (
        "## Current rules\n\n"
        + rules_listing
        + "\n\n---\n\n"
        + _summaries_block(summaries)
    )
    raw = client.extract_structured(DELTA_PROMPT, user_message, DELTA_TOOL_SCHEMA)
    deltas: list[RuleDelta] = []
    for item in raw.get("operations", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            deltas.append(RuleDelta(**{**item, "session_ids": tuple(item.get("session_ids") or ())}))
        except ValueError:
            continue
    return deltas

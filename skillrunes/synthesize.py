"""Skill file synthesis layer for SkillRunes.

Responsible for:
- Taking all session summaries and the current SKILL.md content
- Calling the model client to produce an improved SKILL.md
- Returning both the new content and a short change summary for versioning

All model calls go through client.ModelClient — never instantiate SDK clients
here directly.  No file I/O is performed here.
"""

import json

from skillrunes.client import get_client
from skillrunes.models import SessionSummary

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
) -> str:
    """Build the user-turn message that provides context to the synthesis call."""
    parts: list[str] = []

    if current_skill:
        parts.append("## Current SKILL.md\n\n" + current_skill)
    else:
        parts.append(
            "## Current SKILL.md\n\n"
            "(No existing SKILL.md — create one from scratch based on the sessions below.)"
        )

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
        }
        for s in summaries
    ]
    parts.append(
        f"## New session summaries ({len(summaries)} session(s))\n\n"
        + json.dumps(summary_dicts, indent=2)
    )

    return "\n\n---\n\n".join(parts)


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
    client = get_client()
    user_message = _build_user_message(summaries, current_skill)
    new_content = client.generate_text(SYNTHESIS_PROMPT, user_message)
    change_summary = _extract_change_summary(new_content)
    return new_content, change_summary

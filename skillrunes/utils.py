"""Shared utility helpers for SkillRunes.

Contains:
- Path sanitization helpers for matching ~/.claude/projects/ folder names
- compute_metrics(): pure function that aggregates SessionSummary → ProjectMetrics
- diff helpers for displaying SKILL.md changes
"""

import difflib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from skillrunes.models import PatternCount, ProjectMetrics, SessionSummary, TimePoint


def sanitize_path_to_dirname(path: str | Path) -> str:
    """Convert an absolute path to the folder-name format used by Claude Code.

    Claude Code sanitizes the cwd by replacing path separators and dots with
    hyphens, e.g. /Users/yash/Documents/SkillRunes → -Users-yash-Documents-SkillRunes.

    This is used only as a fast pre-filter when scanning ~/.claude/projects/.
    Always verify the match by reading the cwd field inside the JSONL lines
    themselves, because the sanitization is lossy (a literal '-' in a path is
    indistinguishable from a '/').
    """
    return str(path).replace("/", "-").replace(".", "-")


def compute_metrics(summaries: list[SessionSummary]) -> ProjectMetrics:
    """Aggregate a list of SessionSummary objects into ProjectMetrics.

    Pure function with no I/O — called by the CLI and the result is persisted
    via store.save_metrics().
    """
    if not summaries:
        return ProjectMetrics()

    sorted_summaries = sorted(summaries, key=lambda s: s.analyzed_at)

    # Success rate: proxy is "at least one thing worked and nothing failed" → 1.0,
    # partial success (something worked but also failures) → 0.5,
    # all failures → 0.0
    success_rate_series: list[TimePoint] = []
    token_series: list[TimePoint] = []

    for s in sorted_summaries:
        if s.what_worked and not s.what_failed:
            rate = 1.0
        elif s.what_worked and s.what_failed:
            rate = 0.5
        else:
            rate = 0.0
        success_rate_series.append(TimePoint(date=s.analyzed_at, value=rate))
        token_series.append(TimePoint(date=s.analyzed_at, value=float(s.token_count)))

    # Top failure patterns: count how many sessions each failure string appeared in
    all_failures = [f for s in summaries for f in s.what_failed]
    pattern_counts = Counter(all_failures)
    top_failure_patterns = [
        PatternCount(pattern=pattern, count=count)
        for pattern, count in pattern_counts.most_common(5)
    ]

    return ProjectMetrics(
        sessions_analyzed=len(summaries),
        success_rate_series=success_rate_series,
        token_series=token_series,
        top_failure_patterns=top_failure_patterns,
    )


def now_utc() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(tz=timezone.utc)


def render_skill_diff(old_content: str, new_content: str, version: int) -> str:
    """Produce a unified diff string between two SKILL.md versions.

    The output is a plain unified diff suitable for Rich markup or HTML rendering.
    """
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"SKILL.md (v{version - 1})",
        tofile=f"SKILL.md (v{version})",
    )
    return "".join(diff)

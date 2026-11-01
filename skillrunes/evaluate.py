"""Deterministic SkillRunes memory quality checks."""

from __future__ import annotations

import re
from collections import Counter

from skillrunes.compress import estimate_tokens
from skillrunes.models import EvalResult, SessionSummary, SkillVersion
from skillrunes.redact import scan_text
from skillrunes.utils import now_utc


def evaluate_skill_versions(
    before: SkillVersion,
    after: SkillVersion,
    summaries: list[SessionSummary],
) -> EvalResult:
    """Compare two skill versions using deterministic local checks."""
    before_failures = _count_repeated_failures(before.content, summaries)
    after_failures = _count_repeated_failures(after.content, summaries)
    before_dupes = _duplicate_line_count(before.content)
    after_dupes = _duplicate_line_count(after.content)
    after_tokens = estimate_tokens(after.content)
    before_tokens = estimate_tokens(before.content)
    redaction_counts = scan_text(after.content, "basic")
    pattern_coverage = _pattern_coverage(after.content, summaries)

    metrics: dict[str, float | int | str | bool] = {
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "token_delta": after_tokens - before_tokens,
        "before_repeated_failures": before_failures,
        "after_repeated_failures": after_failures,
        "repeated_failure_delta": after_failures - before_failures,
        "before_duplicate_lines": before_dupes,
        "after_duplicate_lines": after_dupes,
        "duplicate_delta": after_dupes - before_dupes,
        "pattern_coverage": round(pattern_coverage, 3),
        "redaction_findings": sum(redaction_counts.values()),
    }
    checks = {
        "no_new_repeated_failures": after_failures <= before_failures,
        "no_duplicate_growth": after_dupes <= before_dupes,
        "redaction_clean": sum(redaction_counts.values()) == 0,
        "contains_reusable_patterns": pattern_coverage > 0,
    }
    return EvalResult(
        before_version=before.version,
        after_version=after.version,
        metrics=metrics,
        checks=checks,
        generated_at=now_utc(),
    )


def latest_pair(history: list[SkillVersion]) -> tuple[SkillVersion, SkillVersion]:
    """Return the latest comparable skill pair."""
    if len(history) < 2:
        raise ValueError("At least two skill versions are required to run evals.")
    return history[-2], history[-1]


def version_pair(history: list[SkillVersion], before_version: int, after_version: int) -> tuple[SkillVersion, SkillVersion]:
    by_version = {item.version: item for item in history}
    try:
        return by_version[before_version], by_version[after_version]
    except KeyError as exc:
        raise ValueError("Requested skill version was not found.") from exc


def _duplicate_line_count(content: str) -> int:
    lines = [
        re.sub(r"\s+", " ", line.strip().lower())
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    counts = Counter(lines)
    return sum(count - 1 for count in counts.values() if count > 1)


def _count_repeated_failures(content: str, summaries: list[SessionSummary]) -> int:
    lower = content.lower()
    failures = Counter(f for s in summaries for f in s.what_failed)
    return sum(1 for failure, count in failures.items() if count > 1 and failure.lower() in lower)


def _pattern_coverage(content: str, summaries: list[SessionSummary]) -> float:
    patterns = [p for s in summaries for p in s.patterns]
    if not patterns:
        return 0.0
    lower = content.lower()
    hits = sum(1 for pattern in patterns if _loose_contains(lower, pattern.lower()))
    return hits / len(patterns)


def _loose_contains(content: str, pattern: str) -> bool:
    words = [word for word in re.findall(r"[a-z0-9]+", pattern) if len(word) > 4]
    if not words:
        return False
    return sum(1 for word in words if word in content) >= max(1, min(3, len(words)))

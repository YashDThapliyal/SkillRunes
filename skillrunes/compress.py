"""Deterministic token-budgeting for generated project memory."""

from __future__ import annotations

from collections import Counter

from skillrunes.models import CompressionReport, SessionSummary

COMPRESSION_MODES = ("concise", "balanced", "detailed")


def validate_compression_mode(mode: str) -> str:
    if mode not in COMPRESSION_MODES:
        raise ValueError(
            f"Unknown compression mode {mode!r}. Expected: {', '.join(COMPRESSION_MODES)}."
        )
    return mode


def estimate_tokens(text: str) -> int:
    """Cheap token estimate good enough for budgets and before/after deltas."""
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def compress_skill_content(
    content: str,
    *,
    budget_tokens: int | None,
    mode: str = "balanced",
) -> tuple[str, str, CompressionReport]:
    """Compress Markdown to a budget, returning (main, reference, report)."""
    validate_compression_mode(mode)
    before = estimate_tokens(content)
    if budget_tokens is None or budget_tokens <= 0 or before <= budget_tokens:
        report = CompressionReport(
            mode=mode,
            budget_tokens=budget_tokens or before,
            estimated_tokens_before=before,
            estimated_tokens_after=before,
            kept_count=len(_content_lines(content)),
            moved_count=0,
            dropped_count=0,
        )
        return content, "", report

    lines = _content_lines(content)
    scored = [(idx, line, _line_score(line, idx, mode)) for idx, line in enumerate(lines)]
    ordered = sorted(scored, key=lambda item: (-item[2], item[0]))

    keep: set[int] = set()
    current: list[str] = []
    for idx, line, _ in ordered:
        trial = _render_preserving_order(lines, keep | {idx})
        if estimate_tokens(trial) <= budget_tokens:
            keep.add(idx)
            current.append(line)

    if not keep and lines:
        keep.add(0)

    main = _render_preserving_order(lines, keep)
    moved_lines = [line for idx, line in enumerate(lines) if idx not in keep]
    reference = "# SkillRunes Reference Overflow\n\n" + "\n".join(moved_lines) if moved_lines else ""
    after = estimate_tokens(main)

    report = CompressionReport(
        mode=mode,
        budget_tokens=budget_tokens,
        estimated_tokens_before=before,
        estimated_tokens_after=after,
        kept_count=len(keep),
        moved_count=len(moved_lines),
        dropped_count=0,
    )
    return main, reference, report


def build_ranked_memory_seed(summaries: list[SessionSummary], mode: str) -> str:
    """Create deterministic high-signal memory notes to prepend to synthesis context."""
    validate_compression_mode(mode)
    if not summaries:
        return ""

    failures = Counter(f for s in summaries for f in s.what_failed)
    patterns = Counter(p for s in summaries for p in s.patterns)
    worked = Counter(w for s in summaries for w in s.what_worked)

    limit = {"concise": 5, "balanced": 10, "detailed": 18}[mode]
    lines = ["## Ranked Memory Signals"]
    for label, counter in (
        ("Repeated failure", failures),
        ("Reusable pattern", patterns),
        ("Worked strategy", worked),
    ):
        for item, count in counter.most_common(limit):
            lines.append(f"- {label} ({count}x): {item}")

    compactions = sum(1 for s in summaries if s.compaction_occurred)
    if compactions:
        lines.append(f"- Context pressure: {compactions} session(s) had compaction.")
    return "\n".join(lines)


def _content_lines(content: str) -> list[str]:
    return [line.rstrip() for line in content.splitlines() if line.strip()]


def _line_score(line: str, index: int, mode: str) -> int:
    lower = line.lower()
    score = 0
    if line.startswith("#"):
        score += 80
    if any(word in lower for word in ("must", "always", "never", "avoid", "before")):
        score += 30
    if any(word in lower for word in ("failed", "failure", "error", "test", "compaction")):
        score += 25
    if any(word in lower for word in ("worked", "pattern", "remember", "use")):
        score += 15
    if len(line) < 160:
        score += 8
    if mode == "concise" and len(line) > 220:
        score -= 20
    if mode == "detailed":
        score += max(0, 20 - index // 5)
    else:
        score += max(0, 10 - index // 10)
    return score


def _render_preserving_order(lines: list[str], keep: set[int]) -> str:
    return "\n".join(line for idx, line in enumerate(lines) if idx in keep).strip() + "\n"

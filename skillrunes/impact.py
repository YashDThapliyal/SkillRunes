"""Measure whether each SKILL.md version changed agent behaviour.

Sessions are bucketed into cohorts by which skill version was active when the
session started.  Each cohort's deterministic signals (tool error rate,
retries, corrections, interruptions, tokens) are compared with the previous
cohort using a bootstrap confidence interval on the difference of means.

This is observational, not a controlled experiment: task mix changes over time
too.  It answers "did things get better after vN?" cheaply from data the user
already has; `skillrunes bench` is the controlled counterpart.

No model calls and no file I/O happen here.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from datetime import datetime, timezone

from skillrunes.models import (
    ImpactReport,
    MetricDelta,
    RawMessage,
    SessionSummary,
    SkillVersion,
    VersionCohort,
)
from skillrunes.signals import compute_signals
from skillrunes.stats import bootstrap_mean_diff, mean
from skillrunes.utils import now_utc

DEFAULT_MIN_SESSIONS = 3

# Every metric is "lower is better".
METRICS: dict[str, Callable[[SessionSummary], float]] = {
    "tool_error_rate": lambda s: s.signals.tool_error_rate if s.signals else 0.0,
    "retries_per_session": lambda s: float(s.signals.retry_loops) if s.signals else 0.0,
    "correction_rate": lambda s: s.signals.correction_rate if s.signals else 0.0,
    "interruptions_per_session": lambda s: float(s.signals.interruptions) if s.signals else 0.0,
    "tokens_per_session": lambda s: float(s.token_count),
}


def backfill_summary(
    summary: SessionSummary,
    raw_lines: list[dict] | None,
    to_messages: Callable[[list[dict]], list[RawMessage]],
) -> SessionSummary | None:
    """Return a copy of summary with signals/started_at rebuilt from raw lines.

    Returns None when nothing needs (or can be) backfilled.
    """
    if summary.signals is not None and summary.started_at is not None:
        return None
    if not raw_lines:
        return None
    messages = to_messages(raw_lines)
    if not messages:
        return None
    timestamps = [m.timestamp for m in messages if m.timestamp is not None]
    return summary.model_copy(
        update={
            "signals": summary.signals or compute_signals(messages),
            "started_at": summary.started_at or (min(timestamps) if timestamps else None),
        }
    )


def build_impact_report(
    summaries: list[SessionSummary],
    history: list[SkillVersion],
    *,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
) -> ImpactReport:
    """Bucket measured sessions by active skill version and compare cohorts."""
    measured = [s for s in summaries if s.signals is not None and s.started_at is not None]
    starts = [_as_utc(v.timestamp) for v in sorted(history, key=lambda v: v.version)]
    versions = [v.version for v in sorted(history, key=lambda v: v.version)]

    buckets: dict[int, list[SessionSummary]] = {}
    for summary in measured:
        index = bisect_right(starts, _as_utc(summary.started_at))  # type: ignore[arg-type]
        version = versions[index - 1] if index > 0 else 0
        buckets.setdefault(version, []).append(summary)

    cohorts = [
        _cohort(version, buckets.get(version, []), starts, versions)
        for version in [0, *versions]
        if buckets.get(version)
    ]
    deltas = [
        delta
        for before, after in zip(cohorts, cohorts[1:])
        for delta in _compare(before, after, buckets, min_sessions)
    ]
    return ImpactReport(
        cohorts=cohorts,
        deltas=deltas,
        unmeasured_sessions=len(summaries) - len(measured),
        min_sessions=min_sessions,
        generated_at=now_utc(),
    )


def _cohort(
    version: int,
    sessions: list[SessionSummary],
    starts: list[datetime],
    versions: list[int],
) -> VersionCohort:
    active_from = None if version == 0 else starts[versions.index(version)]
    position = versions.index(version) + 1 if version else 0
    active_until = starts[position] if position < len(starts) else None
    return VersionCohort(
        version=version,
        active_from=active_from,
        active_until=active_until,
        session_ids=sorted(s.session_id for s in sessions),
        metrics={name: round(mean([fn(s) for s in sessions]), 4) for name, fn in METRICS.items()},
    )


def _compare(
    before: VersionCohort,
    after: VersionCohort,
    buckets: dict[int, list[SessionSummary]],
    min_sessions: int,
) -> list[MetricDelta]:
    before_sessions = buckets[before.version]
    after_sessions = buckets[after.version]
    enough = min(len(before_sessions), len(after_sessions)) >= min_sessions
    deltas: list[MetricDelta] = []
    for name, fn in METRICS.items():
        before_values = [fn(s) for s in before_sessions]
        after_values = [fn(s) for s in after_sessions]
        base = MetricDelta(
            metric=name,
            before_version=before.version,
            after_version=after.version,
            before_mean=round(mean(before_values), 4),
            after_mean=round(mean(after_values), 4),
            delta=round(mean(after_values) - mean(before_values), 4),
        )
        if not enough:
            deltas.append(base)
            continue
        interval = bootstrap_mean_diff(before_values, after_values)
        deltas.append(
            base.model_copy(
                update={
                    "ci_low": round(interval.low, 4),
                    "ci_high": round(interval.high, 4),
                    "verdict": _verdict(interval.low, interval.high),
                }
            )
        )
    return deltas


def _verdict(low: float, high: float) -> str:
    if high < 0:
        return "improved"
    if low > 0:
        return "regressed"
    return "inconclusive"


def _as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC so comparisons never raise."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

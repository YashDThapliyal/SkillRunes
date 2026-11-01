"""Aggregate benchmark results into per-arm summaries and paired comparisons.

Arms are compared on identical (task, seed) pairs with a paired bootstrap, so
per-task difficulty cancels out.  The promotion gate is a non-inferiority
test: a candidate instruction file is promoted only if its pass-rate gain over
the baseline is non-negative AND the lower CI bound is above -margin.
"""

from __future__ import annotations

from collections import defaultdict

from skillrunes.models import (
    ArmComparison,
    ArmSummary,
    BenchReport,
    BenchRunResult,
)
from skillrunes.stats import Interval, bootstrap_mean, bootstrap_paired_diff, mean
from skillrunes.utils import now_utc

DEFAULT_GATE_MARGIN = 0.05


def build_report(
    results: list[BenchRunResult],
    suite: str,
    split: str,
    baseline: str | None = None,
) -> BenchReport:
    """Summarise every arm and compare each non-baseline arm with baseline."""
    selected = [r for r in results if split == "all" or r.split == split]
    by_arm: dict[str, list[BenchRunResult]] = defaultdict(list)
    for result in selected:
        by_arm[result.arm].append(result)

    arms = [_summarise(name, runs) for name, runs in sorted(by_arm.items())]
    comparisons: list[ArmComparison] = []
    if baseline and baseline in by_arm:
        comparisons = [
            comparison
            for name in sorted(by_arm)
            if name != baseline
            for comparison in [_compare(by_arm[name], by_arm[baseline], name, baseline)]
            if comparison is not None
        ]
    return BenchReport(
        suite=suite, split=split, arms=arms, comparisons=comparisons, generated_at=now_utc()
    )


def gate_decision(
    report: BenchReport, candidate: str, margin: float = DEFAULT_GATE_MARGIN
) -> tuple[bool, str]:
    """Return (promote, reason) for candidate against the report's baseline."""
    comparison = next((c for c in report.comparisons if c.candidate == candidate), None)
    if comparison is None:
        return False, f"No paired comparison for arm {candidate!r}."
    low = comparison.pass_rate_ci[0]
    if comparison.pass_rate_delta < 0:
        return False, f"pass rate fell by {-comparison.pass_rate_delta:.1%}."
    if low <= -margin:
        return False, (
            f"pass-rate CI lower bound {low:+.1%} is below the -{margin:.0%} "
            "non-inferiority margin; need more runs or a better skill."
        )
    return True, (
        f"pass rate {comparison.pass_rate_delta:+.1%} "
        f"(95% CI {low:+.1%} to {comparison.pass_rate_ci[1]:+.1%})."
    )


def _summarise(arm: str, runs: list[BenchRunResult]) -> ArmSummary:
    passes = [1.0 if r.passed else 0.0 for r in runs]
    interval = _proportion_interval(passes)
    criteria: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        for name, ok in run.criteria.items():
            criteria[name].append(1.0 if ok else 0.0)
    first = [r.attempts[0] for r in runs if r.attempts]
    return ArmSummary(
        arm=arm,
        runs=len(runs),
        pass_rate=round(mean(passes), 4),
        pass_rate_ci=(round(interval.low, 4), round(interval.high, 4)),
        criteria_rates={name: round(mean(vals), 4) for name, vals in sorted(criteria.items())},
        mean_turns=round(mean([a.num_turns for a in first]), 2),
        mean_tokens=round(mean([a.total_tokens for a in first]), 1),
        mean_tool_errors=round(mean([a.signals.tool_errors for a in first]), 3),
        mean_retries=round(mean([a.signals.retry_loops for a in first]), 3),
        mean_duration_seconds=round(mean([a.duration_seconds for a in first]), 1),
        agent_errors=sum(1 for a in first if a.is_error),
    )


def _proportion_interval(values: list[float]) -> Interval:
    """Bootstrap CI for a pass rate (mean of 0/1 outcomes)."""
    return bootstrap_mean(values) if values else Interval(0.0, 0.0, 0.0)


def _compare(
    candidate_runs: list[BenchRunResult],
    baseline_runs: list[BenchRunResult],
    candidate: str,
    baseline: str,
) -> ArmComparison | None:
    base = {(r.task_id, r.seed): r for r in baseline_runs}
    pairs = [(base[(r.task_id, r.seed)], r) for r in candidate_runs if (r.task_id, r.seed) in base]
    if not pairs:
        return None

    def paired(metric) -> Interval:
        return bootstrap_paired_diff([(metric(b), metric(c)) for b, c in pairs])

    passed = paired(lambda r: 1.0 if r.passed else 0.0)
    tokens = paired(lambda r: float(r.attempts[0].total_tokens) if r.attempts else 0.0)
    turns = paired(lambda r: float(r.attempts[0].num_turns) if r.attempts else 0.0)
    return ArmComparison(
        candidate=candidate,
        baseline=baseline,
        pairs=len(pairs),
        pass_rate_delta=round(passed.estimate, 4),
        pass_rate_ci=(round(passed.low, 4), round(passed.high, 4)),
        tokens_delta=round(tokens.estimate, 1),
        tokens_ci=(round(tokens.low, 1), round(tokens.high, 1)),
        turns_delta=round(turns.estimate, 2),
        turns_ci=(round(turns.low, 2), round(turns.high, 2)),
    )

"""Small, dependency-free statistics helpers.

Bootstrap confidence intervals are used instead of t-tests because SkillRunes
cohorts are small and per-session metrics are heavily skewed (most sessions
have zero retries, a few have many).  All resampling is seeded so reports are
reproducible run-to-run.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_RESAMPLES = 2000
DEFAULT_CONFIDENCE = 0.95
DEFAULT_SEED = 0


@dataclass(frozen=True)
class Interval:
    """A point estimate with a confidence interval."""

    estimate: float
    low: float
    high: float

    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def bootstrap_mean(
    values: Sequence[float],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """CI for the mean of values (e.g. a pass rate over 0/1 outcomes)."""
    if not values:
        raise ValueError("bootstrap_mean needs at least one value.")
    rng = random.Random(seed)
    samples = sorted(mean(rng.choices(values, k=len(values))) for _ in range(resamples))
    low, high = _percentile_bounds(samples, confidence)
    return Interval(mean(values), low, high)


def bootstrap_mean_diff(
    before: Sequence[float],
    after: Sequence[float],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """CI for mean(after) - mean(before) with independent resampling."""
    if not before or not after:
        raise ValueError("bootstrap_mean_diff needs at least one value in each group.")
    rng = random.Random(seed)
    diffs = sorted(
        mean(rng.choices(after, k=len(after))) - mean(rng.choices(before, k=len(before)))
        for _ in range(resamples)
    )
    low, high = _percentile_bounds(diffs, confidence)
    return Interval(mean(after) - mean(before), low, high)


def bootstrap_paired_diff(
    pairs: Sequence[tuple[float, float]],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """CI for the mean of (b - a) over paired observations (a, b).

    Pairs are resampled together, which removes per-task difficulty from the
    variance — the right test when both arms ran the same tasks.
    """
    if not pairs:
        raise ValueError("bootstrap_paired_diff needs at least one pair.")
    diffs = [b - a for a, b in pairs]
    rng = random.Random(seed)
    samples = sorted(mean(rng.choices(diffs, k=len(diffs))) for _ in range(resamples))
    low, high = _percentile_bounds(samples, confidence)
    return Interval(mean(diffs), low, high)


def _percentile_bounds(sorted_values: Sequence[float], confidence: float) -> tuple[float, float]:
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1.")
    tail = (1 - confidence) / 2
    last = len(sorted_values) - 1
    low_index = int(round(tail * last))
    high_index = int(round((1 - tail) * last))
    return sorted_values[low_index], sorted_values[high_index]

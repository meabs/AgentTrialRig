"""Small-sample statistics without dependencies.

Agent runs are expensive, so trials are small and costs vary a lot from run to run. These helpers say how far to trust
a difference: a bootstrap interval for a mean, a bootstrap interval for the ratio of two means, and the number of runs
per arm needed to detect a given difference.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist, mean, median, stdev

SEED = 20260101
RESAMPLES = 4000


def _pct(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def summary(xs: list[float]) -> dict:
    n = len(xs)
    if n == 0:
        return {"n": 0}
    m = mean(xs)
    sd = stdev(xs) if n > 1 else 0.0
    out = {"n": n, "mean": m, "median": median(xs), "sd": sd, "cv": (sd / m) if m else None, "min": min(xs), "max": max(xs)}
    out["ci95"] = mean_ci(xs)
    return out


def mean_ci(xs: list[float], level: float = 0.95) -> tuple[float, float] | None:
    if len(xs) < 2:
        return None
    rng = random.Random(SEED)
    means = sorted(mean(rng.choices(xs, k=len(xs))) for _ in range(RESAMPLES))
    a = (1 - level) / 2
    return (_pct(means, a), _pct(means, 1 - a))


def ratio_ci(xs: list[float], base: list[float], level: float = 0.95) -> dict | None:
    """mean(xs) / mean(base), with a bootstrap interval. `clear` is true when the interval excludes 1."""
    if len(xs) < 2 or len(base) < 2 or mean(base) == 0:
        return None
    rng = random.Random(SEED + 1)
    ratios = []
    for _ in range(RESAMPLES):
        b = mean(rng.choices(base, k=len(base)))
        if b:
            ratios.append(mean(rng.choices(xs, k=len(xs))) / b)
    ratios.sort()
    a = (1 - level) / 2
    lo, hi = _pct(ratios, a), _pct(ratios, 1 - a)
    return {"ratio": mean(xs) / mean(base), "lo": lo, "hi": hi, "clear": not (lo <= 1.0 <= hi)}


def runs_needed(cv: float, effect: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Runs per arm to detect a relative difference of `effect` (0.2 = 20%) in means, given a coefficient of variation.

    Normal approximation for a two-sample comparison: n = 2 (z_a + z_b)^2 cv^2 / effect^2.
    """
    z = NormalDist().inv_cdf
    return max(2, math.ceil(2 * (z(1 - alpha / 2) + z(power)) ** 2 * cv**2 / effect**2))


def proportion_text(k: int, n: int) -> str:
    return f"{k}/{n}"

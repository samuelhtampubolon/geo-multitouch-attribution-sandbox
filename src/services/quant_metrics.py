"""
Quantitative helper metrics shared by the GEO auditor and the attribution engine.

- Wilson score interval for a binomial proportion (citation rate confidence bounds)
- Herfindahl-Hirschman Index (market concentration of share of voice)
- Shannon entropy (spread of citations across engines)
- Jensen-Shannon divergence and total variation distance (disagreement between attribution models)
- Percentile interval helper for bootstrap resampling
"""

from __future__ import annotations
import math
from typing import Iterable, Mapping, Sequence

Z_95 = 1.959963984540054  # two-sided 95% standard normal quantile


def wilson_interval(successes: float, trials: float, z: float = Z_95) -> tuple[float, float]:
    """
    Wilson score interval for a binomial proportion p = k / n:
        center = (p + z^2 / 2n) / (1 + z^2 / n)
        half   = z * sqrt(p(1 - p) / n + z^2 / 4n^2) / (1 + z^2 / n)
    Unlike the Wald interval it stays inside [0, 1] and behaves well for small n or extreme p.
    """
    if trials <= 0:
        return 0.0, 0.0
    p = min(1.0, max(0.0, successes / trials))
    z2 = z * z
    denom = 1.0 + z2 / trials
    center = (p + z2 / (2.0 * trials)) / denom
    half = (z * math.sqrt(p * (1.0 - p) / trials + z2 / (4.0 * trials * trials))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def normalize(values: Mapping[str, float]) -> dict[str, float]:
    """Scales non-negative weights to a probability distribution (uniform if all zero)."""
    total = sum(max(0.0, v) for v in values.values())
    if total <= 0.0:
        n = len(values)
        return {k: (1.0 / n if n else 0.0) for k in values}
    return {k: max(0.0, v) / total for k, v in values.items()}


def herfindahl_index(shares: Iterable[float]) -> float:
    """HHI = 10000 * sum(s_i^2) for shares s_i summing to 1. 10000 = monopoly, 10000/n = perfectly even."""
    share_list = [max(0.0, s) for s in shares]
    total = sum(share_list)
    if total <= 0.0:
        return 0.0
    return 10000.0 * sum((s / total) ** 2 for s in share_list)


def shannon_entropy_bits(weights: Iterable[float]) -> float:
    """H = -sum(p_i * log2 p_i) of the normalized weights."""
    w = [max(0.0, x) for x in weights]
    total = sum(w)
    if total <= 0.0:
        return 0.0
    return -sum((x / total) * math.log2(x / total) for x in w if x > 0.0)


def kl_divergence_bits(p: Sequence[float], q: Sequence[float]) -> float:
    """Kullback-Leibler divergence D(p || q) in bits (terms with p_i = 0 contribute 0)."""
    total = 0.0
    for pi, qi in zip(p, q):
        if pi > 0.0:
            if qi <= 0.0:
                return math.inf
            total += pi * math.log2(pi / qi)
    return total


def jensen_shannon_bits(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """
    JSD(P || Q) = 1/2 KL(P || M) + 1/2 KL(Q || M), M = (P + Q) / 2.
    Symmetric, always finite, bounded in [0, 1] bit.
    """
    keys = sorted(set(p) | set(q))
    pn = normalize({k: p.get(k, 0.0) for k in keys})
    qn = normalize({k: q.get(k, 0.0) for k in keys})
    pv = [pn[k] for k in keys]
    qv = [qn[k] for k in keys]
    m = [(a + b) / 2.0 for a, b in zip(pv, qv)]
    return max(0.0, 0.5 * kl_divergence_bits(pv, m) + 0.5 * kl_divergence_bits(qv, m))


def total_variation(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """TV(P, Q) = 1/2 sum |p_i - q_i|: the share of credit that changes hands between two models."""
    keys = set(p) | set(q)
    pn = normalize({k: p.get(k, 0.0) for k in keys})
    qn = normalize({k: q.get(k, 0.0) for k in keys})
    return 0.5 * sum(abs(pn[k] - qn[k]) for k in keys)


def percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile (numpy 'linear' method) of an already sorted sequence."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    frac = rank - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)

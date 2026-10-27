"""
Tests for quantitative metrics (Wilson CI, HHI, entropy, divergences) and the concave
marginal-ROAS budget optimizer (KKT optimality, bounds, closed form, monotone improvement).
"""

from __future__ import annotations
import math
import random

import pytest

from src.services.budget_optimizer import marginal_roas, optimize_budget, response_revenue
from src.services.quant_metrics import (
    herfindahl_index,
    jensen_shannon_bits,
    percentile,
    shannon_entropy_bits,
    total_variation,
    wilson_interval,
)


# --- Quant metrics ---------------------------------------------------------

def test_wilson_interval_reference_values():
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4) and hi == pytest.approx(0.5962, abs=1e-4)
    lo, hi = wilson_interval(0, 20)
    assert lo == 0.0 and hi == pytest.approx(0.1611, abs=1e-4)
    lo, hi = wilson_interval(20, 20)
    assert hi == 1.0 and lo == pytest.approx(0.8389, abs=1e-4)
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_wilson_interval_narrows_with_sample_size():
    w_small = wilson_interval(13, 20)
    w_large = wilson_interval(650, 1000)
    assert (w_large[1] - w_large[0]) < (w_small[1] - w_small[0])
    for lo, hi in (w_small, w_large):
        assert lo <= 0.65 <= hi


def test_herfindahl_index_extremes():
    assert herfindahl_index([1.0]) == pytest.approx(10000.0)
    assert herfindahl_index([0.25] * 4) == pytest.approx(2500.0)
    assert herfindahl_index([2, 2, 2, 2]) == pytest.approx(2500.0)  # normalizes raw weights
    assert herfindahl_index([]) == 0.0


def test_shannon_entropy_bits():
    assert shannon_entropy_bits([1, 1, 1, 1]) == pytest.approx(2.0)
    assert shannon_entropy_bits([5, 0, 0]) == pytest.approx(0.0)
    assert shannon_entropy_bits([1, 1]) == pytest.approx(1.0)


def test_jensen_shannon_properties():
    p = {"a": 0.7, "b": 0.2, "c": 0.1}
    q = {"a": 0.1, "b": 0.3, "c": 0.6}
    assert jensen_shannon_bits(p, p) == pytest.approx(0.0, abs=1e-12)
    assert jensen_shannon_bits(p, q) == pytest.approx(jensen_shannon_bits(q, p))
    assert jensen_shannon_bits({"a": 1.0}, {"b": 1.0}) == pytest.approx(1.0)  # disjoint support = 1 bit
    assert 0.0 < jensen_shannon_bits(p, q) < 1.0


def test_total_variation_properties():
    p = {"a": 0.5, "b": 0.5}
    q = {"a": 0.9, "b": 0.1}
    assert total_variation(p, q) == pytest.approx(0.4)
    assert total_variation(p, p) == 0.0
    assert total_variation({"a": 1.0}, {"b": 1.0}) == pytest.approx(1.0)


def test_percentile_matches_linear_interpolation():
    data = sorted([3.0, 1.0, 4.0, 1.5, 9.0])
    assert percentile(data, 0) == 1.0 and percentile(data, 100) == 9.0
    assert percentile(data, 50) == 3.0
    assert percentile(data, 25) == pytest.approx(1.5)


# --- Budget optimizer ------------------------------------------------------

REV = {"AI Search": 9000.0, "Paid Social": 6000.0, "Blog": 4000.0, "Influencer": 2500.0}
SPEND = {"AI Search": 3500.0, "Paid Social": 8000.0, "Blog": 2000.0, "Influencer": 4500.0}


def test_unconstrained_optimum_matches_closed_form():
    beta = 0.5
    budget = sum(SPEND.values())
    res = optimize_budget(REV, SPEND, budget, beta=beta, trust_region=100.0)
    a = {c: REV[c] / SPEND[c] ** beta for c in SPEND}
    expo = 1.0 / (1.0 - beta)
    denom = sum(v ** expo for v in a.values())
    for c in SPEND:
        assert res.allocation[c] == pytest.approx(budget * a[c] ** expo / denom, rel=1e-6)


def test_kkt_marginal_roas_equalized_for_interior_channels():
    res = optimize_budget(REV, SPEND, 18000.0, beta=0.6, trust_region=0.5)
    interior = [c for c in SPEND if res.lower_bounds[c] + 1e-6 < res.allocation[c] < res.upper_bounds[c] - 1e-6]
    assert interior, "expected at least one interior channel"
    for c in interior:
        assert res.marginal_roas_optimized[c] == pytest.approx(res.equilibrium_marginal_roas, rel=1e-5)
    # Channels pinned at the upper bound must have mROAS >= lambda, at the lower bound <= lambda
    for c in SPEND:
        if abs(res.allocation[c] - res.upper_bounds[c]) < 1e-6:
            assert res.marginal_roas_optimized[c] >= res.equilibrium_marginal_roas * (1 - 1e-6)
        if abs(res.allocation[c] - res.lower_bounds[c]) < 1e-6:
            assert res.marginal_roas_optimized[c] <= res.equilibrium_marginal_roas * (1 + 1e-6)


@pytest.mark.parametrize("budget", [9000.0, 18000.0, 40000.0])
@pytest.mark.parametrize("trust", [0.0, 0.3, 1.0])
def test_budget_constraint_and_bounds_hold(budget, trust):
    res = optimize_budget(REV, SPEND, budget, beta=0.5, trust_region=trust)
    assert sum(res.allocation.values()) == pytest.approx(budget, rel=1e-9)
    for c in SPEND:
        assert res.lower_bounds[c] - 1e-6 <= res.allocation[c] <= res.upper_bounds[c] + 1e-6


def test_optimizer_never_worse_than_proportional_allocation():
    res = optimize_budget(REV, SPEND, 18000.0, beta=0.5, trust_region=0.5)
    assert res.projected_revenue_optimized >= res.projected_revenue_current - 1e-9
    assert res.projected_lift_pct >= 0.0


def test_optimizer_beats_random_feasible_allocations():
    """Global optimality of the concave program: no random feasible point does better."""
    beta, budget, trust = 0.5, 18000.0, 0.5
    res = optimize_budget(REV, SPEND, budget, beta=beta, trust_region=trust)
    a = {c: REV[c] / SPEND[c] ** beta for c in SPEND}
    rng = random.Random(3)
    channels = sorted(SPEND)
    for _ in range(300):
        x = {c: rng.uniform(res.lower_bounds[c], res.upper_bounds[c]) for c in channels}
        # project onto the budget hyperplane by shifting free mass, then re-clip
        scale = budget / sum(x.values())
        x = {c: min(res.upper_bounds[c], max(res.lower_bounds[c], v * scale)) for c, v in x.items()}
        if abs(sum(x.values()) - budget) > 1.0:
            continue
        candidate = sum(response_revenue(a[c], x[c], beta) for c in channels)
        assert candidate <= res.projected_revenue_optimized + 1e-6


def test_zero_trust_region_keeps_proportional_mix():
    res = optimize_budget(REV, SPEND, 22500.0, beta=0.5, trust_region=0.0)
    scale = 22500.0 / sum(SPEND.values())
    for c in SPEND:
        assert res.allocation[c] == pytest.approx(SPEND[c] * scale)
    assert res.projected_lift_pct == pytest.approx(0.0, abs=1e-9)


def test_marginal_roas_is_beta_times_average_roas_at_calibration_point():
    beta = 0.5
    a = REV["Blog"] / SPEND["Blog"] ** beta
    assert marginal_roas(a, SPEND["Blog"], beta) == pytest.approx(beta * REV["Blog"] / SPEND["Blog"])
    assert response_revenue(a, SPEND["Blog"], beta) == pytest.approx(REV["Blog"])
    assert math.isinf(marginal_roas(a, 0.0, beta))


def test_optimizer_validates_beta():
    with pytest.raises(ValueError):
        optimize_budget(REV, SPEND, 1000.0, beta=1.0)
    with pytest.raises(ValueError):
        optimize_budget(REV, SPEND, 1000.0, beta=0.5, trust_region=-1)

"""
Unit and algorithmic tests for Markov Chain Removal Effects, Shapley Values, rule-based
attribution models and the end-to-end MTA pipeline. Wherever possible results are checked
against hand-derived values or an independent algorithm, not just against themselves.
"""

from __future__ import annotations
import math
import time

import numpy as np
import pytest

from src.config import settings
from src.schemas import MtaComputeRequest
from src.services.geo_auditor import GeoAuditorService
from src.services.markov_shapley_mta import MultiTouchAttributionService as MTA
from tests.conftest import journey

CHANNELS_5 = ["AI Search", "Organic Tech Blog", "Paid Social", "Virtual Influencer", "Direct"]


def channels_of(journeys):
    return sorted({c for j in journeys for c in j.path})


# ----------------------------------------------------------------------------
# Original contract tests (kept)
# ----------------------------------------------------------------------------

def test_markov_transition_matrix_stochastic(sample_journeys):
    """Verify that every transient row in the Markov transition matrix sums to 1.0."""
    probs, states = MTA.build_transition_matrix(sample_journeys, CHANNELS_5)
    for state in states:
        row_sum = sum(probs[state].values())
        assert abs(row_sum - 1.0) < 1e-6, f"Row for state '{state}' does not sum to 1.0 (got {row_sum})"


def test_markov_removal_effects_sum_to_one(sample_journeys):
    """CONTRACT: normalized Markov attribution weights sum to 1.0 within 1e-6 precision."""
    markov_shares, _, _, base_p = MTA.compute_markov_attribution(sample_journeys, CHANNELS_5)
    assert abs(sum(markov_shares.values()) - 1.0) < 1e-6
    assert base_p > 0.0
    assert all(share >= 0.0 for share in markov_shares.values())


def test_shapley_exact_efficiency_and_sum_to_one(sample_journeys):
    """CONTRACT: normalized Shapley attribution weights sum to 1.0 within 1e-6 precision."""
    shapley_shares = MTA.compute_shapley_attribution(sample_journeys, CHANNELS_5)
    assert abs(sum(shapley_shares.values()) - 1.0) < 1e-6
    assert all(share >= 0.0 for share in shapley_shares.values())


def test_last_click_vs_markov_upper_funnel_reallocation():
    """Markov and Shapley credit the upper-funnel touchpoint (AI Search) that Last-Click ignores."""
    journeys = [
        journey(["AI Search", "Paid Social"], True, 100.0, 1),
        journey(["AI Search", "Paid Social"], True, 100.0, 2),
        journey(["AI Search"], False, 0.0, 3),
    ]
    channels = ["AI Search", "Paid Social"]
    last_click, first_touch, _ = MTA.compute_heuristic_attributions(journeys, channels)
    markov_shares, _, _, _ = MTA.compute_markov_attribution(journeys, channels)
    shapley_shares = MTA.compute_shapley_attribution(journeys, channels)

    assert last_click["Paid Social"] == 1.0 and last_click["AI Search"] == 0.0
    assert first_touch["AI Search"] == 1.0
    assert markov_shares["AI Search"] > 0.20
    assert shapley_shares["AI Search"] > 0.20


def test_share_of_model_governing_equation():
    """SoM = sum(w_i * 1/log2(rank_i + 1) * (1 + 0.5 s_i)) / M."""
    assert abs(GeoAuditorService.position_decay(1) - 1.0) < 1e-12
    assert abs(GeoAuditorService.position_decay(2) - 1.0 / math.log2(3)) < 1e-12
    assert abs(GeoAuditorService.sentiment_multiplier(1.0) - 1.5) < 1e-12
    assert abs(GeoAuditorService.sentiment_multiplier(0.0) - 1.0) < 1e-12
    assert abs(GeoAuditorService.sentiment_multiplier(-1.0) - 0.5) < 1e-12
    citations = [{"rank": 1, "sentiment_score": 1.0}, {"rank": 2, "sentiment_score": 0.0}]
    expected = (1.5 + 1.0 / math.log2(3)) / 10.0
    assert abs(GeoAuditorService.calculate_som_score(citations, 10) - expected) < 1e-4


# ----------------------------------------------------------------------------
# Markov chain: analytic and independent-algorithm verification
# ----------------------------------------------------------------------------

def hand_example():
    """
    start->A 3/4, start->B 1/4; A->conv 1/3, A->B 1/3, A->null 1/3; B->conv 1/2, B->null 1/2.
    P(conv) = 1/2; without A = 1/8 (R_A = 3/4); without B = 1/4 (R_B = 1/2); W = (0.6, 0.4).
    """
    return [
        journey(["A"], True, 100.0, 1),
        journey(["A", "B"], True, 100.0, 2),
        journey(["B"], False, 0.0, 3),
        journey(["A"], False, 0.0, 4),
    ]


def test_markov_hand_computed_removal_effects():
    shares, effects, matrix, base = MTA.compute_markov_attribution(hand_example(), ["A", "B"])
    assert base == pytest.approx(0.5, abs=1e-12)
    assert effects["A"] == pytest.approx(0.75, abs=1e-12)
    assert effects["B"] == pytest.approx(0.5, abs=1e-12)
    assert shares["A"] == pytest.approx(0.6, abs=1e-12)
    assert shares["B"] == pytest.approx(0.4, abs=1e-12)
    assert matrix["(start)"]["A"] == pytest.approx(0.75)
    assert matrix["A"]["(conversion)"] == pytest.approx(1 / 3, abs=1e-6)


def test_markov_vectorized_solver_matches_legacy_dictionary_solver(seeded_journeys):
    channels = channels_of(seeded_journeys)
    probs, states = MTA.build_transition_matrix(seeded_journeys, channels)
    _, effects, _, base = MTA.compute_markov_attribution(seeded_journeys, channels)
    legacy_base = MTA.calculate_conversion_probability(probs, states)
    assert base == pytest.approx(legacy_base, abs=1e-12)
    for ch in channels:
        legacy = 1.0 - MTA.calculate_conversion_probability(probs, states, removed_channel=ch) / legacy_base
        assert effects[ch] == pytest.approx(max(0.0, legacy), abs=1e-12)


def test_markov_absorption_matches_independent_value_iteration(seeded_journeys):
    """Fundamental-matrix solution equals fixed-point iteration b <- Q b + r (no matrix inverse)."""
    channels = channels_of(seeded_journeys)
    probs, states = MTA.build_transition_matrix(seeded_journeys, channels)
    transient = [s for s in states if s not in (MTA.CONV_NODE, MTA.NULL_NODE)]
    b = {s: 0.0 for s in transient}
    for _ in range(5000):
        b = {s: probs[s][MTA.CONV_NODE] + sum(probs[s][t] * b[t] for t in transient) for s in transient}
    _, _, _, base = MTA.compute_markov_attribution(seeded_journeys, channels)
    assert base == pytest.approx(b[MTA.START_NODE], abs=1e-10)


@pytest.mark.parametrize("order", [1, 2, 3])
def test_markov_baseline_equals_empirical_conversion_rate(seeded_journeys, order):
    """Flow conservation: the maximum-likelihood chain reproduces the observed conversion rate exactly."""
    channels = channels_of(seeded_journeys)
    _, _, _, base = MTA.compute_markov_attribution(seeded_journeys, channels, order=order)
    empirical = sum(j.converted for j in seeded_journeys) / len(seeded_journeys)
    assert base == pytest.approx(empirical, abs=1e-12)


@pytest.mark.parametrize("order", [1, 2])
def test_markov_expected_visits_equal_empirical_touch_frequencies(seeded_journeys, order):
    """Row 'start' of N = (I - Q)^-1 equals observed touches per journey for every channel."""
    channels = channels_of(seeded_journeys)
    visits, path_length = MTA.markov_expected_visits(seeded_journeys, channels, order=order)
    n = len(seeded_journeys)
    for ch in channels:
        observed = sum(j.path.count(ch) for j in seeded_journeys) / n
        assert visits[ch] == pytest.approx(observed, abs=1e-10)
    assert path_length == pytest.approx(sum(len(j.path) for j in seeded_journeys) / n, abs=1e-10)


def test_markov_full_memory_order_reproduces_exact_counterfactual():
    """
    With order >= the longest path every state is a full history prefix, so removing channel k
    yields exactly the conversion rate of journeys that never touched k.
    """
    journeys = [
        journey(["A", "B", "C"], True, 1, 1), journey(["B", "A"], False, 0, 2), journey(["C"], True, 1, 3),
        journey(["A", "C"], True, 1, 4), journey(["B"], False, 0, 5), journey(["C", "B", "A"], True, 1, 6),
        journey(["A"], False, 0, 7), journey(["B", "C"], True, 1, 8),
    ]
    channels = ["A", "B", "C"]
    model = MTA.fit_markov_chain(journeys, channels, order=3)
    probs = MTA._transition_probabilities(model, model.journey_weights)
    n = len(journeys)
    for ch in channels:
        expected = sum(j.converted and ch not in j.path for j in journeys) / n
        assert MTA._conversion_probability(model, probs, ch) == pytest.approx(expected, abs=1e-12)


def test_markov_order_k_identical_for_single_touch_paths():
    journeys = [journey(["A"], True, 1, 1), journey(["B"], False, 0, 2), journey(["A"], False, 0, 3), journey(["B"], True, 1, 4)]
    s1, e1, _, b1 = MTA.compute_markov_attribution(journeys, ["A", "B"], order=1)
    s2, e2, _, b2 = MTA.compute_markov_attribution(journeys, ["A", "B"], order=2)
    assert b1 == pytest.approx(b2)
    for ch in ("A", "B"):
        assert s1[ch] == pytest.approx(s2[ch]) and e1[ch] == pytest.approx(e2[ch])


@pytest.mark.parametrize("order", [1, 2, 3])
def test_markov_removal_effects_bounded_and_shares_sum_to_one(seeded_journeys, order):
    channels = channels_of(seeded_journeys)
    shares, effects, matrix, _ = MTA.compute_markov_attribution(seeded_journeys, channels, order=order)
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(0.0 <= e <= 1.0 for e in effects.values())
    for state, row in matrix.items():
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-5), state


def test_markov_zero_conversions_falls_back_to_uniform():
    journeys = [journey(["A", "B"], False, 0, 1), journey(["B"], False, 0, 2)]
    shares, effects, _, base = MTA.compute_markov_attribution(journeys, ["A", "B"])
    assert base == 0.0
    assert shares == {"A": 0.5, "B": 0.5}
    assert effects == {"A": 0.0, "B": 0.0}


def test_bootstrap_ci_brackets_point_estimate_and_is_reproducible(seeded_journeys):
    channels = channels_of(seeded_journeys)
    shares, _, _, _ = MTA.compute_markov_attribution(seeded_journeys, channels)
    ci_a = MTA.bootstrap_markov_shares(seeded_journeys, channels, samples=150, seed=11)
    ci_b = MTA.bootstrap_markov_shares(seeded_journeys, channels, samples=150, seed=11)
    assert ci_a == ci_b
    for ch in channels:
        lo, hi = ci_a[ch]
        assert 0.0 <= lo <= hi <= 1.0
        assert lo - 0.01 <= shares[ch] <= hi + 0.01
        assert hi - lo < 0.15  # 500 journeys give a reasonably tight interval


def test_markov_computation_is_sub_100ms(seeded_journeys):
    """NFR-02: vectorized attribution on the 500-journey corpus stays under 100 ms."""
    channels = channels_of(seeded_journeys)
    timings = []
    for _ in range(5):
        start = time.perf_counter()
        MTA.compute_markov_attribution(seeded_journeys, channels)
        timings.append(time.perf_counter() - start)
    assert min(timings) < 0.100


# ----------------------------------------------------------------------------
# Shapley values: closed form, axioms, hand example
# ----------------------------------------------------------------------------

def test_shapley_hand_computed_example():
    """v(A)=50, v(B)=0, v(AB)=150 -> phi_A = (50 + 150)/2 = 100, phi_B = (0 + 100)/2 = 50."""
    journeys = [journey(["A", "B"], True, 100.0, 1), journey(["A"], True, 50.0, 2), journey(["B"], False, 0, 3)]
    phi = MTA.compute_shapley_values(journeys, ["A", "B"], method="exact")
    assert phi["A"] == pytest.approx(100.0)
    assert phi["B"] == pytest.approx(50.0)


def test_shapley_exact_enumeration_equals_closed_form(seeded_journeys):
    channels = channels_of(seeded_journeys)
    exact = MTA.compute_shapley_values(seeded_journeys, channels, method="exact")
    closed = MTA.compute_shapley_values(seeded_journeys, channels, method="closed_form")
    for ch in channels:
        assert exact[ch] == pytest.approx(closed[ch], rel=1e-10, abs=1e-8)


def test_shapley_efficiency_axiom(seeded_journeys):
    """sum_k phi_k = v(N) - v(empty) = total converted revenue."""
    channels = channels_of(seeded_journeys)
    phi = MTA.compute_shapley_values(seeded_journeys, channels, method="exact")
    total_value = sum(j.conversion_value_usd for j in seeded_journeys if j.converted)
    assert sum(phi.values()) == pytest.approx(total_value, rel=1e-12)


def test_shapley_symmetry_and_dummy_axioms():
    journeys = [
        journey(["A", "B"], True, 80.0, 1), journey(["B", "A", "C"], True, 60.0, 2),
        journey(["D"], False, 0.0, 3), journey(["C"], True, 30.0, 4),
    ]
    phi = MTA.compute_shapley_values(journeys, ["A", "B", "C", "D"], method="exact")
    assert phi["A"] == pytest.approx(phi["B"])     # A and B always appear together -> symmetric
    assert phi["D"] == pytest.approx(0.0)          # D never contributes to a conversion -> dummy


def test_shapley_additivity_axiom(seeded_journeys):
    """phi(v1 + v2) = phi(v1) + phi(v2): splitting the journeys splits the game additively."""
    channels = channels_of(seeded_journeys)
    half = len(seeded_journeys) // 2
    phi_all = MTA.compute_shapley_values(seeded_journeys, channels, method="exact")
    phi_1 = MTA.compute_shapley_values(seeded_journeys[:half], channels, method="exact")
    phi_2 = MTA.compute_shapley_values(seeded_journeys[half:], channels, method="exact")
    for ch in channels:
        assert phi_all[ch] == pytest.approx(phi_1[ch] + phi_2[ch], rel=1e-10, abs=1e-8)


def test_shapley_large_channel_sets_never_drop_channels():
    """Above MAX_SHAPLEY_CHANNELS the exact closed form is used instead of truncating the channel list."""
    n = settings.MAX_SHAPLEY_CHANNELS + 3
    channels = [f"ch{i:02d}" for i in range(n)]
    journeys = [journey([channels[i], channels[(i + 1) % n]], True, 10.0, i) for i in range(n)]
    shares = MTA.compute_shapley_attribution(journeys, channels)
    assert set(shares) == set(channels)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert all(s == pytest.approx(1.0 / n) for s in shares.values())  # cyclic symmetry
    with pytest.raises(ValueError):
        MTA.compute_shapley_values(journeys, channels, method="exact")


# ----------------------------------------------------------------------------
# Rule-based models
# ----------------------------------------------------------------------------

def test_position_based_weights():
    assert MTA.position_based_weights(1) == [1.0]
    assert MTA.position_based_weights(2) == [0.5, 0.5]
    assert MTA.position_based_weights(3) == pytest.approx([0.4, 0.2, 0.4])
    assert MTA.position_based_weights(5) == pytest.approx([0.4, 0.2 / 3, 0.2 / 3, 0.2 / 3, 0.4])
    for n in range(1, 10):
        assert sum(MTA.position_based_weights(n)) == pytest.approx(1.0)


def test_time_decay_weights_half_life():
    w = MTA.time_decay_weights(3, half_life=1.0)  # raw 1/4, 1/2, 1 -> /1.75
    assert w == pytest.approx([0.25 / 1.75, 0.5 / 1.75, 1.0 / 1.75])
    w2 = MTA.time_decay_weights(4, half_life=2.0)
    assert w2[-1] / w2[-3] == pytest.approx(2.0)  # two touches earlier -> half the credit
    assert sum(w2) == pytest.approx(1.0)


def test_all_rule_based_models_sum_to_one(seeded_journeys):
    channels = channels_of(seeded_journeys)
    lc, ft, lin = MTA.compute_heuristic_attributions(seeded_journeys, channels)
    pb, td = MTA.compute_positional_attributions(seeded_journeys, channels)
    for dist in (lc, ft, lin, pb, td):
        assert sum(dist.values()) == pytest.approx(1.0, abs=1e-5)
        assert all(v >= 0.0 for v in dist.values())


# ----------------------------------------------------------------------------
# End-to-end pipeline
# ----------------------------------------------------------------------------

def test_pipeline_end_to_end_invariants(seeded_journeys):
    req = MtaComputeRequest(tenant_id="tenant-alpha", journeys=seeded_journeys, bootstrap_samples=50, persist=False)
    res = MTA.execute_mta_pipeline(req)
    shares = [c.markov_share for c in res.channels]
    assert sum(shares) == pytest.approx(1.0, abs=1e-3)
    assert sum(c.shapley_share for c in res.channels) == pytest.approx(1.0, abs=1e-3)
    assert shares == sorted(shares, reverse=True)
    assert res.baseline_conversion_rate == pytest.approx(res.empirical_conversion_rate, abs=1e-4)
    assert sum(c.recommended_budget_reallocation_usd for c in res.channels) == pytest.approx(res.total_spend_usd, abs=0.05)
    assert res.projected_revenue_optimized_usd >= res.projected_revenue_current_usd - 1e-6
    assert sum(c.attributed_revenue_markov for c in res.channels) == pytest.approx(res.total_revenue_usd, abs=0.1)
    assert all(c.markov_share_ci_low is not None for c in res.channels)
    assert {d.model for d in res.model_divergence_vs_markov} >= {"Last-Click", "Shapley", "Time-Decay"}


def test_pipeline_rejects_too_many_channels():
    journeys = [journey([f"c{i}"], True, 1.0, i) for i in range(settings.MAX_DISTINCT_CHANNELS + 1)]
    with pytest.raises(ValueError):
        MTA.execute_mta_pipeline(MtaComputeRequest(tenant_id="tenant-alpha", journeys=journeys, persist=False))


def test_pipeline_numerically_stable_with_repeated_self_loops():
    journeys = [journey(["A"] * 25, True, 10.0, 1), journey(["A", "B"] * 12, False, 0.0, 2), journey(["B"], True, 5.0, 3)]
    res = MTA.execute_mta_pipeline(MtaComputeRequest(tenant_id="tenant-alpha", journeys=journeys, persist=False))
    assert all(np.isfinite(c.markov_share) for c in res.channels)
    assert res.baseline_conversion_rate == pytest.approx(2 / 3, abs=1e-4)

"""
Unit and algorithmic tests for Markov Chain Removal Effects, Shapley Values, and SoM scores.
"""

from __future__ import annotations
import math
import pytest
from src.schemas import CustomerJourneyInput
from src.services.geo_auditor import GeoAuditorService
from src.services.markov_shapley_mta import MultiTouchAttributionService


@pytest.fixture
def sample_journeys() -> list[CustomerJourneyInput]:
    """Sample realistic multi-touch conversion journeys."""
    return [
        CustomerJourneyInput(session_hash="sess_001", path=["AI Search", "Organic Tech Blog", "Direct"], converted=True, conversion_value_usd=200.0),
        CustomerJourneyInput(session_hash="sess_002", path=["AI Search", "Paid Social"], converted=True, conversion_value_usd=150.0),
        CustomerJourneyInput(session_hash="sess_003", path=["Paid Social", "Direct"], converted=True, conversion_value_usd=180.0),
        CustomerJourneyInput(session_hash="sess_004", path=["Virtual Influencer", "AI Search", "Direct"], converted=True, conversion_value_usd=300.0),
        CustomerJourneyInput(session_hash="sess_005", path=["Organic Tech Blog", "Paid Social"], converted=False, conversion_value_usd=0.0),
        CustomerJourneyInput(session_hash="sess_006", path=["Paid Social"], converted=False, conversion_value_usd=0.0),
        CustomerJourneyInput(session_hash="sess_007", path=["Virtual Influencer"], converted=False, conversion_value_usd=0.0),
        CustomerJourneyInput(session_hash="sess_008", path=["AI Search"], converted=False, conversion_value_usd=0.0),
    ]


def test_markov_transition_matrix_stochastic(sample_journeys):
    """Verify that every transient row in the Markov transition matrix sums to 1.0."""
    channels = ["AI Search", "Organic Tech Blog", "Paid Social", "Virtual Influencer", "Direct"]
    probs, states = MultiTouchAttributionService.build_transition_matrix(sample_journeys, channels)

    for state in states:
        row_sum = sum(probs[state].values())
        assert abs(row_sum - 1.0) < 1e-6, f"Row for state '{state}' does not sum to 1.0 (got {row_sum})"


def test_markov_removal_effects_sum_to_one(sample_journeys):
    """
    CRITICAL MANDATORY CONTRACT CHECK:
    Verify that normalized Markov attribution weights sum to 1.0 within 1e-6 precision.
    """
    channels = ["AI Search", "Organic Tech Blog", "Paid Social", "Virtual Influencer", "Direct"]
    markov_shares, removal_effects, _, base_p = MultiTouchAttributionService.compute_markov_attribution(
        sample_journeys, channels
    )

    total_share = sum(markov_shares.values())
    assert abs(total_share - 1.0) < 1e-6, f"Markov shares sum to {total_share}, expected 1.0 within 1e-6"
    assert base_p > 0.0, "Baseline conversion probability must be positive"
    for ch, share in markov_shares.items():
        assert share >= 0.0, f"Share for channel {ch} cannot be negative"


def test_shapley_exact_efficiency_and_sum_to_one(sample_journeys):
    """
    CRITICAL MANDATORY CONTRACT CHECK:
    Verify that normalized Shapley attribution weights sum to 1.0 within 1e-6 precision.
    """
    channels = ["AI Search", "Organic Tech Blog", "Paid Social", "Virtual Influencer", "Direct"]
    shapley_shares = MultiTouchAttributionService.compute_shapley_attribution(sample_journeys, channels)

    total_shapley = sum(shapley_shares.values())
    assert abs(total_shapley - 1.0) < 1e-6, f"Shapley shares sum to {total_shapley}, expected 1.0 within 1e-6"
    for ch, share in shapley_shares.items():
        assert share >= 0.0, f"Shapley share for {ch} cannot be negative"


def test_last_click_vs_markov_upper_funnel_reallocation():
    """
    Verify that Markov and Shapley credit upper-funnel touchpoints (AI Search)
    that Last-Click completely ignores.
    """
    journeys = [
        CustomerJourneyInput(session_hash="sess_t01", path=["AI Search", "Paid Social"], converted=True, conversion_value_usd=100.0),
        CustomerJourneyInput(session_hash="sess_t02", path=["AI Search", "Paid Social"], converted=True, conversion_value_usd=100.0),
        CustomerJourneyInput(session_hash="sess_t03", path=["AI Search"], converted=False, conversion_value_usd=0.0),
    ]
    channels = ["AI Search", "Paid Social"]

    last_click, first_touch, linear = MultiTouchAttributionService.compute_heuristic_attributions(journeys, channels)
    markov_shares, _, _, _ = MultiTouchAttributionService.compute_markov_attribution(journeys, channels)
    shapley_shares = MultiTouchAttributionService.compute_shapley_attribution(journeys, channels)

    # Last-click gives 100% credit to Paid Social, 0% to AI Search
    assert last_click["Paid Social"] == 1.0
    assert last_click["AI Search"] == 0.0

    # First-touch gives 100% credit to AI Search
    assert first_touch["AI Search"] == 1.0

    # Markov and Shapley both attribute substantial credit (> 20%) to AI Search
    assert markov_shares["AI Search"] > 0.20, f"Markov should credit AI Search (got {markov_shares['AI Search']})"
    assert shapley_shares["AI Search"] > 0.20, f"Shapley should credit AI Search (got {shapley_shares['AI Search']})"


def test_share_of_model_governing_equation():
    """
    Verify SoM equation:
    sum(citation_weight_i * position_decay_i * sentiment_multiplier_i) / total_category_prompts
    """
    # 1. Test position decay: rank 1 is 1.0, rank 2 is 1/log2(3) ~ 0.6309
    decay_1 = GeoAuditorService.position_decay(1)
    decay_2 = GeoAuditorService.position_decay(2)
    assert abs(decay_1 - 1.0) < 1e-6
    assert abs(decay_2 - (1.0 / math.log2(3))) < 1e-6
    assert decay_1 > decay_2

    # 2. Test sentiment multiplier: +1 -> 1.5, 0 -> 1.0, -1 -> 0.5
    assert abs(GeoAuditorService.sentiment_multiplier(1.0) - 1.5) < 1e-6
    assert abs(GeoAuditorService.sentiment_multiplier(0.0) - 1.0) < 1e-6
    assert abs(GeoAuditorService.sentiment_multiplier(-1.0) - 0.5) < 1e-6

    # 3. Test calculation with known mock citations
    citations = [
        {"rank": 1, "sentiment_score": 1.0},  # weight 1 * decay 1.0 * mult 1.5 = 1.5
        {"rank": 2, "sentiment_score": 0.0},  # weight 1 * decay (1/log2(3)) * mult 1.0 = 0.6309
    ]
    total_prompts = 10
    som = GeoAuditorService.calculate_som_score(citations, total_prompts)
    expected = (1.5 + (1.0 / math.log2(3))) / 10.0
    assert abs(som - expected) < 1e-4

"""
Multi-Touch Attribution (MTA) Service:
- First-order Markov Chain with Total Removal Effect
- Exact Shapley Value Attribution (Cooperative Game Theory)
- Last-Click, First-Touch, Linear Models
- ROAS & Budget Reallocation Optimization
"""

from __future__ import annotations
import itertools
import math
from typing import Any, Optional
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from src.config import settings
from src.models import AttributionResult, ChannelSpend, CustomerJourney
from src.schemas import (
    ChannelAttributionShare,
    CustomerJourneyInput,
    ChannelSpendInput,
    MtaComputeRequest,
    MtaComputeResponse
)


class MultiTouchAttributionService:
    """Computes Algorithmic Marketing Attribution across customer conversion journeys."""

    START_NODE = "(start)"
    CONV_NODE = "(conversion)"
    NULL_NODE = "(null)"

    @classmethod
    def build_transition_matrix(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str]
    ) -> tuple[dict[str, dict[str, float]], list[str]]:
        """
        Constructs a first-order Markov transition probability matrix.
        Rows are stochastic (sum to 1.0 for transient states).
        """
        states = [cls.START_NODE] + sorted(channels) + [cls.CONV_NODE, cls.NULL_NODE]
        counts: dict[str, dict[str, int]] = {s: {s2: 0 for s2 in states} for s in states}

        for j in journeys:
            clean_path = [cls.START_NODE] + [c for c in j.path if c in channels]
            terminal = cls.CONV_NODE if j.converted else cls.NULL_NODE
            clean_path.append(terminal)

            for i in range(len(clean_path) - 1):
                from_state = clean_path[i]
                to_state = clean_path[i + 1]
                counts[from_state][to_state] += 1

        # Absorbing states transition to themselves
        counts[cls.CONV_NODE][cls.CONV_NODE] = 1
        counts[cls.NULL_NODE][cls.NULL_NODE] = 1

        probs: dict[str, dict[str, float]] = {s: {} for s in states}
        for s in states:
            total_out = sum(counts[s].values())
            for s2 in states:
                probs[s][s2] = (counts[s][s2] / total_out) if total_out > 0 else (1.0 if s == s2 else 0.0)

        return probs, states

    @classmethod
    def calculate_conversion_probability(
        cls,
        probs: dict[str, dict[str, float]],
        states: list[str],
        removed_channel: Optional[str] = None
    ) -> float:
        """
        Computes P(Conversion | Start) using Absorbing Markov Chain fundamental matrix:
        Q = transient -> transient
        R = transient -> absorbing [Conversion, Null]
        Fundamental Matrix N = (I - Q)^(-1)
        B = N * R
        """
        transient = [s for s in states if s not in (cls.CONV_NODE, cls.NULL_NODE)]
        dim = len(transient)
        if dim == 0:
            return 0.0

        Q = np.zeros((dim, dim), dtype=np.float64)
        R = np.zeros((dim, 2), dtype=np.float64)  # col 0: Conv, col 1: Null

        trans_map = {s: idx for idx, s in enumerate(transient)}

        for s in transient:
            s_idx = trans_map[s]
            if s == removed_channel:
                # If channel is removed, entering it transitions 100% to NULL
                R[s_idx, 1] = 1.0
                continue

            for s2 in transient:
                s2_idx = trans_map[s2]
                Q[s_idx, s2_idx] = probs[s].get(s2, 0.0)

            R[s_idx, 0] = probs[s].get(cls.CONV_NODE, 0.0)
            R[s_idx, 1] = probs[s].get(cls.NULL_NODE, 0.0)

        I = np.eye(dim, dtype=np.float64)
        try:
            # (I - Q) can be inverted
            N = np.linalg.inv(I - Q)
            B = np.dot(N, R)
            start_idx = trans_map[cls.START_NODE]
            p_conv = float(B[start_idx, 0])
            return max(0.0, min(1.0, p_conv))
        except np.linalg.LinAlgError:
            # SVD pseudo-inverse fallback
            N = np.linalg.pinv(I - Q)
            B = np.dot(N, R)
            start_idx = trans_map[cls.START_NODE]
            return max(0.0, min(1.0, float(B[start_idx, 0])))

    @classmethod
    def compute_markov_attribution(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str]
    ) -> tuple[dict[str, float], dict[str, float], dict[str, dict[str, float]], float]:
        """
        Computes Total Removal Effect for every marketing channel:
        R_k = 1.0 - (P_conversion_without_k / P_conversion_full_graph)
        Normalized share: W_k = R_k / sum(R_j)
        Returns: (markov_shares, removal_effects, transition_matrix, baseline_p_conv)
        """
        probs, states = cls.build_transition_matrix(journeys, channels)
        base_p_conv = cls.calculate_conversion_probability(probs, states, removed_channel=None)

        removal_effects: dict[str, float] = {}
        for ch in channels:
            p_without = cls.calculate_conversion_probability(probs, states, removed_channel=ch)
            if base_p_conv > 1e-9:
                re = max(0.0, 1.0 - (p_without / base_p_conv))
            else:
                re = 0.0
            removal_effects[ch] = re

        total_re = sum(removal_effects.values())
        markov_shares: dict[str, float] = {}
        if total_re > 1e-9:
            for ch in channels:
                markov_shares[ch] = removal_effects[ch] / total_re
        else:
            # Uniform fallback if no removal effects exist
            uniform = 1.0 / len(channels) if channels else 0.0
            for ch in channels:
                markov_shares[ch] = uniform

        return markov_shares, removal_effects, probs, base_p_conv

    @classmethod
    def compute_shapley_attribution(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str]
    ) -> dict[str, float]:
        """
        Exact Shapley Value Attribution using Cooperative Game Theory:
        phi_k = sum_{S subset N \\ {k}} (|S|! * (|N| - |S| - 1)! / |N|!) * [v(S U {k}) - v(S)]
        Guarantees efficiency: sum(phi_k) = v(N)
        Normalized shares sum to 1.0 within 1e-6.
        """
        n = len(channels)
        if n == 0:
            return {}
        if n > settings.MAX_SHAPLEY_CHANNELS:
            # Cap combinatorial complexity to protect against DoS
            channels = channels[:settings.MAX_SHAPLEY_CHANNELS]
            n = len(channels)

        channel_set = set(channels)
        factorial_n = math.factorial(n)

        # Pre-process journeys: map each journey to its set of channels and conversion value
        journey_data: list[tuple[frozenset[str], float]] = []
        for j in journeys:
            if j.converted:
                s = frozenset(c for c in j.path if c in channel_set)
                val = j.conversion_value_usd if j.conversion_value_usd > 0.0 else 1.0
                journey_data.append((s, val))

        # Evaluate characteristic function v(S) for all coalitions S
        # Memoize v(S) using powerset
        coalition_values: dict[frozenset[str], float] = {}
        all_subsets = []
        for r in range(n + 1):
            for comb in itertools.combinations(channels, r):
                s = frozenset(comb)
                all_subsets.append(s)
                # v(S) is total conversion value from paths whose channel set is subset of S
                val = sum(v for j_set, v in journey_data if j_set.issubset(s))
                coalition_values[s] = val

        shapley_values: dict[str, float] = {ch: 0.0 for ch in channels}

        for ch in channels:
            remaining = [c for c in channels if c != ch]
            ch_set = frozenset([ch])
            phi_ch = 0.0

            for r in range(n):
                weight = (math.factorial(r) * math.factorial(n - r - 1)) / factorial_n
                for comb in itertools.combinations(remaining, r):
                    S = frozenset(comb)
                    marginal_contribution = coalition_values[S | ch_set] - coalition_values[S]
                    phi_ch += weight * marginal_contribution

            shapley_values[ch] = max(0.0, phi_ch)

        # Normalize Shapley values so they sum to 1.0
        total_shapley = sum(shapley_values.values())
        shapley_shares: dict[str, float] = {}
        if total_shapley > 1e-9:
            for ch in channels:
                shapley_shares[ch] = shapley_values[ch] / total_shapley
        else:
            uniform = 1.0 / n
            for ch in channels:
                shapley_shares[ch] = uniform

        return shapley_shares

    @classmethod
    def compute_heuristic_attributions(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str]
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
        """
        Computes Last-Click, First-Touch, and Linear attribution shares.
        """
        last_clicks = {ch: 0.0 for ch in channels}
        first_touches = {ch: 0.0 for ch in channels}
        linear_touches = {ch: 0.0 for ch in channels}

        conv_journeys = [j for j in journeys if j.converted]
        if not conv_journeys:
            uniform = 1.0 / len(channels) if channels else 0.0
            return ({ch: uniform for ch in channels},
                    {ch: uniform for ch in channels},
                    {ch: uniform for ch in channels})

        for j in conv_journeys:
            valid_path = [c for c in j.path if c in channels]
            if not valid_path:
                continue

            # First touch
            first_touches[valid_path[0]] += 1.0

            # Last touch
            last_clicks[valid_path[-1]] += 1.0

            # Linear touch
            share_per_touch = 1.0 / len(valid_path)
            for ch in valid_path:
                linear_touches[ch] += share_per_touch

        def normalize(d: dict[str, float]) -> dict[str, float]:
            total = sum(d.values())
            if total > 0:
                return {k: round(v / total, 6) for k, v in d.items()}
            return {k: 1.0 / len(d) for k in d}

        return normalize(last_clicks), normalize(first_touches), normalize(linear_touches)

    @classmethod
    def execute_mta_pipeline(
        cls,
        request: MtaComputeRequest,
        db: Optional[Session] = None
    ) -> MtaComputeResponse:
        """
        Full end-to-end MTA pipeline:
        1. Ingests or queries journeys and spends
        2. Solves Markov removal effects & transition matrix
        3. Computes exact Shapley values
        4. Calculates heuristic models (Last-click, First-touch, Linear)
        5. Computes ROAS and budget reallocation recommendations
        """
        # 1. Load journeys
        journeys = request.journeys
        if not journeys and db is not None:
            db_journeys = (
                db.query(CustomerJourney)
                .filter(CustomerJourney.tenant_id == request.tenant_id)
                .limit(1000)
                .all()
            )
            import json
            journeys = [
                CustomerJourneyInput(
                    session_hash=j.session_hash,
                    path=json.loads(j.path_sequence_json),
                    converted=j.converted,
                    conversion_value_usd=j.conversion_value_usd
                )
                for j in db_journeys
            ]

        if not journeys:
            raise ValueError(f"No customer journeys found for tenant '{request.tenant_id}'.")

        # Discover active channels
        discovered_channels = set()
        for j in journeys:
            for c in j.path:
                discovered_channels.add(c)
        channel_list = sorted(list(discovered_channels))

        # 2. Load channel spend
        spend_map: dict[str, float] = {ch: 5000.0 for ch in channel_list}
        if request.channel_spends:
            for cs in request.channel_spends:
                spend_map[cs.channel_name] = cs.monthly_spend_usd
        elif db is not None:
            db_spends = (
                db.query(ChannelSpend)
                .filter(ChannelSpend.tenant_id == request.tenant_id)
                .all()
            )
            for cs in db_spends:
                spend_map[cs.channel_name] = cs.monthly_spend_usd

        total_conversions = sum(1 for j in journeys if j.converted)
        total_revenue = sum(j.conversion_value_usd for j in journeys if j.converted)
        if total_revenue == 0.0 and total_conversions > 0:
            total_revenue = total_conversions * 150.0  # default synthetic AOV

        total_spend = sum(spend_map.get(ch, 0.0) for ch in channel_list)
        if request.total_budget_usd:
            total_spend = request.total_budget_usd

        # 3. Model Computations
        markov_shares, removal_effects, trans_matrix, base_p_conv = cls.compute_markov_attribution(
            journeys, channel_list
        )
        shapley_shares = cls.compute_shapley_attribution(journeys, channel_list)
        last_click, first_touch, linear = cls.compute_heuristic_attributions(journeys, channel_list)

        # 4. ROAS & Reallocation
        channels_result: list[ChannelAttributionShare] = []
        reallocation_weights: dict[str, float] = {}

        for ch in channel_list:
            spend = spend_map.get(ch, 1000.0)
            m_share = markov_shares.get(ch, 0.0)
            s_share = shapley_shares.get(ch, 0.0)
            lc_share = last_click.get(ch, 0.0)
            ft_share = first_touch.get(ch, 0.0)
            lin_share = linear.get(ch, 0.0)

            attr_rev_markov = m_share * total_revenue
            attr_conv_markov = m_share * total_conversions

            roas_lc = (lc_share * total_revenue) / spend if spend > 0 else 0.0
            roas_m = attr_rev_markov / spend if spend > 0 else 0.0
            roas_s = (s_share * total_revenue) / spend if spend > 0 else 0.0

            # Reallocation score combines Markov weight with marginal ROAS
            marginal_weight = m_share * (roas_m ** 0.5 if roas_m > 0 else 0.1)
            reallocation_weights[ch] = marginal_weight

            # Categorize marginal ROAS
            blended_roas = total_revenue / total_spend if total_spend > 0 else 1.0
            if roas_m > blended_roas * 1.15:
                category_label = "Under-Invested (High Growth)"
            elif roas_m < blended_roas * 0.85:
                category_label = "Over-Invested (Margin Erosion)"
            else:
                category_label = "Optimized (Equilibrium)"

            channels_result.append(ChannelAttributionShare(
                channel_name=ch,
                last_click_share=round(lc_share, 4),
                first_touch_share=round(ft_share, 4),
                linear_share=round(lin_share, 4),
                markov_share=round(m_share, 4),
                shapley_share=round(s_share, 4),
                markov_removal_effect=round(removal_effects.get(ch, 0.0), 4),
                attributed_conversions_markov=round(attr_conv_markov, 1),
                attributed_revenue_markov=round(attr_rev_markov, 2),
                current_spend_usd=round(spend, 2),
                roas_last_click=round(roas_lc, 2),
                roas_markov=round(roas_m, 2),
                roas_shapley=round(roas_s, 2),
                recommended_budget_reallocation_usd=0.0,  # Populated next
                marginal_roas_category=category_label
            ))

        # Normalize budget reallocation to match total_spend exactly
        total_realloc_w = sum(reallocation_weights.values()) or 1.0
        for item in channels_result:
            w = reallocation_weights.get(item.channel_name, 0.0) / total_realloc_w
            item.recommended_budget_reallocation_usd = round(w * total_spend, 2)

        # Sort channels by Markov share descending
        channels_result.sort(key=lambda x: x.markov_share, reverse=True)

        blended_roas = round(total_revenue / total_spend, 2) if total_spend > 0 else 0.0

        # Persist attribution results if DB session provided
        if db is not None:
            # Clear old records for this tenant
            db.query(AttributionResult).filter(AttributionResult.tenant_id == request.tenant_id).delete()
            for item in channels_result:
                db_record = AttributionResult(
                    tenant_id=request.tenant_id,
                    channel_name=item.channel_name,
                    last_click_share=item.last_click_share,
                    first_touch_share=item.first_touch_share,
                    linear_share=item.linear_share,
                    markov_share=item.markov_share,
                    shapley_share=item.shapley_share,
                    attributed_revenue_markov=item.attributed_revenue_markov,
                    attributed_revenue_shapley=round(item.shapley_share * total_revenue, 2),
                    roas_markov=item.roas_markov,
                    roas_shapley=item.roas_shapley,
                    recommended_spend_usd=item.recommended_budget_reallocation_usd
                )
                db.add(db_record)
            db.commit()

        # Generate strategic insight summary
        top_under = [c for c in channels_result if "Under-Invested" in c.marginal_roas_category]
        top_over = [c for c in channels_result if "Over-Invested" in c.marginal_roas_category]
        insight = (
            f"Last-Click attribution systematically undervalues upper-funnel generative touchpoints. "
            f"Under Markov & Shapley models, channels like '{top_under[0].channel_name if top_under else 'AI Search'}' "
            f"demonstrate significant removal effect impact. Recommended budget shift: reallocate spend from "
            f"'{top_over[0].channel_name if top_over else 'Paid Social'}' to maximize marginal blended ROAS."
        )

        return MtaComputeResponse(
            tenant_id=request.tenant_id,
            total_journeys=len(journeys),
            total_conversions=total_conversions,
            baseline_conversion_rate=round(base_p_conv, 4),
            total_revenue_usd=round(total_revenue, 2),
            total_spend_usd=round(total_spend, 2),
            blended_roas=blended_roas,
            channels=channels_result,
            transition_matrix=trans_matrix,
            model_reallocation_insight=insight
        )

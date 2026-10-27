"""
Multi-Touch Attribution (MTA) Service:
- k-th order absorbing Markov chain with Total Removal Effect (vectorized NumPy solver)
- Exact Shapley Value Attribution (2^N coalition enumeration) + exact closed form for large N
- Last-Click, First-Touch, Linear, Position-Based (U-shaped 40/20/40) and Time-Decay models
- Multinomial bootstrap confidence intervals for Markov shares
- Concave ROAS budget optimizer (marginal ROAS equalization, see budget_optimizer.py)
"""

from __future__ import annotations
import itertools
import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from src.config import settings
from src.models import AttributionResult, ChannelSpend, CustomerJourney
from src.schemas import (
    ChannelAttributionShare,
    CustomerJourneyInput,
    ModelDivergence,
    MtaComputeRequest,
    MtaComputeResponse
)
from src.services.budget_optimizer import optimize_budget
from src.services.quant_metrics import jensen_shannon_bits, percentile, total_variation

StateKey = tuple[str, ...]


@dataclass
class MarkovChainModel:
    """Compact representation of an order-k absorbing chain fitted to aggregated journeys."""
    order: int
    states: list[StateKey]               # transient states; index 0 is (start)
    from_idx: np.ndarray                 # transition origin index per (unique journey, step)
    to_idx: np.ndarray                   # destination index; T = conversion, T + 1 = null
    journey_idx: np.ndarray              # which unique journey each transition belongs to
    journey_weights: np.ndarray          # multiplicity of each unique journey
    unique_journeys: list[tuple[StateKey, bool]]

    @property
    def n_transient(self) -> int:
        return len(self.states)


class MultiTouchAttributionService:
    """Computes Algorithmic Marketing Attribution across customer conversion journeys."""

    START_NODE = "(start)"
    CONV_NODE = "(conversion)"
    NULL_NODE = "(null)"

    # ------------------------------------------------------------------
    # Legacy first-order dictionary API (kept for readability & tests)
    # ------------------------------------------------------------------
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
            clean_path.append(cls.CONV_NODE if j.converted else cls.NULL_NODE)
            for i in range(len(clean_path) - 1):
                counts[clean_path[i]][clean_path[i + 1]] += 1

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
        Computes P(Conversion | Start) using the absorbing Markov chain fundamental matrix:
        Q = transient -> transient, R = transient -> absorbing [Conversion, Null]
        N = (I - Q)^(-1), B = N R.  Solved as (I - Q) b = r_conv for numerical stability.
        """
        transient = [s for s in states if s not in (cls.CONV_NODE, cls.NULL_NODE)]
        dim = len(transient)
        if dim == 0:
            return 0.0

        trans_map = {s: idx for idx, s in enumerate(transient)}
        Q = np.zeros((dim, dim), dtype=np.float64)
        r_conv = np.zeros(dim, dtype=np.float64)

        for s in transient:
            if s == removed_channel:
                # Removing a channel turns every visit into a transition to NULL.
                continue
            s_idx = trans_map[s]
            for s2 in transient:
                Q[s_idx, trans_map[s2]] = probs[s].get(s2, 0.0)
            r_conv[s_idx] = probs[s].get(cls.CONV_NODE, 0.0)

        b = cls._solve(np.eye(dim) - Q, r_conv)
        return float(min(1.0, max(0.0, b[trans_map[cls.START_NODE]])))

    @staticmethod
    def _solve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        try:
            return np.linalg.solve(a, b)
        except np.linalg.LinAlgError:
            return np.linalg.pinv(a) @ b

    # ------------------------------------------------------------------
    # Vectorized order-k absorbing Markov chain
    # ------------------------------------------------------------------
    @classmethod
    def fit_markov_chain(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str],
        order: int = 1
    ) -> MarkovChainModel:
        """
        Builds an order-k chain. A state is the tuple of the last k touchpoints, so the path
        A -> B -> C with k = 2 visits (start) -> (A) -> (A, B) -> (B, C) -> absorbing.
        For k = 1 this is exactly the classic first-order channel graph.
        """
        if order < 1:
            raise ValueError("Markov order must be >= 1.")
        channel_set = set(channels)
        aggregated = Counter(
            (tuple(c for c in j.path if c in channel_set), bool(j.converted)) for j in journeys
        )
        unique_journeys = sorted(aggregated.keys())

        start: StateKey = (cls.START_NODE,)
        state_index: dict[StateKey, int] = {start: 0}
        states: list[StateKey] = [start]
        from_idx: list[int] = []
        to_idx: list[int] = []
        journey_idx: list[int] = []
        pending: list[tuple[int, int, object]] = []

        for u, (path, converted) in enumerate(unique_journeys):
            seq: list[StateKey] = [start]
            for i in range(len(path)):
                seq.append(tuple(path[max(0, i - order + 1): i + 1]))
            for s in seq:
                if s not in state_index:
                    state_index[s] = len(states)
                    states.append(s)
            for a, b in zip(seq, seq[1:]):
                pending.append((u, state_index[a], state_index[b]))
            pending.append((u, state_index[seq[-1]], cls.CONV_NODE if converted else cls.NULL_NODE))

        t = len(states)
        for u, a, b in pending:
            journey_idx.append(u)
            from_idx.append(a)
            to_idx.append(t if b == cls.CONV_NODE else (t + 1 if b == cls.NULL_NODE else int(b)))

        weights = np.array([aggregated[k] for k in unique_journeys], dtype=np.float64)
        return MarkovChainModel(
            order=order,
            states=states,
            from_idx=np.array(from_idx, dtype=np.int64),
            to_idx=np.array(to_idx, dtype=np.int64),
            journey_idx=np.array(journey_idx, dtype=np.int64),
            journey_weights=weights,
            unique_journeys=unique_journeys,
        )

    @staticmethod
    def _transition_probabilities(model: MarkovChainModel, weights: np.ndarray) -> np.ndarray:
        """Row-stochastic matrix of shape (T, T + 2): transient states x [transient, conv, null]."""
        t = model.n_transient
        width = t + 2
        flat = model.from_idx * width + model.to_idx
        counts = np.bincount(flat, weights=weights[model.journey_idx], minlength=t * width).reshape(t, width)
        row_sum = counts.sum(axis=1, keepdims=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            probs = np.where(row_sum > 0, counts / np.where(row_sum > 0, row_sum, 1.0), 0.0)
        return probs

    @classmethod
    def _conversion_probability(
        cls, model: MarkovChainModel, probs: np.ndarray, removed: Optional[str] = None
    ) -> float:
        t = model.n_transient
        q = probs[:, :t].copy()
        r = probs[:, t].copy()
        if removed is not None:
            mask = np.array([removed in s for s in model.states], dtype=bool)
            q[mask, :] = 0.0
            r[mask] = 0.0
        b = cls._solve(np.eye(t) - q, r)
        return float(min(1.0, max(0.0, b[0])))

    @classmethod
    def _removal_effects(
        cls, model: MarkovChainModel, channels: list[str], probs: np.ndarray
    ) -> tuple[dict[str, float], float]:
        base = cls._conversion_probability(model, probs)
        effects: dict[str, float] = {}
        for ch in channels:
            if base > 1e-12:
                effects[ch] = max(0.0, 1.0 - cls._conversion_probability(model, probs, ch) / base)
            else:
                effects[ch] = 0.0
        return effects, base

    @staticmethod
    def _normalize_effects(effects: dict[str, float], channels: list[str]) -> dict[str, float]:
        total = sum(effects.values())
        if total > 1e-12:
            return {ch: effects[ch] / total for ch in channels}
        uniform = 1.0 / len(channels) if channels else 0.0
        return {ch: uniform for ch in channels}

    @classmethod
    def compute_markov_attribution(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str],
        order: int = 1
    ) -> tuple[dict[str, float], dict[str, float], dict[str, dict[str, float]], float]:
        """
        Total Removal Effect for every marketing channel:
            R_k = 1 - P(conv | chain without k) / P(conv | full chain)
            W_k = R_k / sum_j R_j
        In an order-k chain, removing channel k redirects every state containing k to NULL.
        Returns: (markov_shares, removal_effects, transition_matrix, baseline_p_conv)
        """
        model = cls.fit_markov_chain(journeys, channels, order)
        probs = cls._transition_probabilities(model, model.journey_weights)
        removal_effects, base_p_conv = cls._removal_effects(model, channels, probs)
        shares = cls._normalize_effects(removal_effects, channels)
        return shares, removal_effects, cls._matrix_as_dict(model, probs), base_p_conv

    @classmethod
    def _matrix_as_dict(cls, model: MarkovChainModel, probs: np.ndarray) -> dict[str, dict[str, float]]:
        names = [" > ".join(s) for s in model.states] + [cls.CONV_NODE, cls.NULL_NODE]
        matrix: dict[str, dict[str, float]] = {}
        for i, s in enumerate(names[: model.n_transient]):
            matrix[s] = {names[j]: round(float(p), 6) for j, p in enumerate(probs[i]) if p > 0.0}
        matrix[cls.CONV_NODE] = {cls.CONV_NODE: 1.0}
        matrix[cls.NULL_NODE] = {cls.NULL_NODE: 1.0}
        return matrix

    @classmethod
    def markov_expected_visits(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str],
        order: int = 1
    ) -> tuple[dict[str, float], float]:
        """
        Expected visits from the fundamental matrix: row 'start' of N = (I - Q)^(-1).
        Visits to channel c = sum of N[start, s] over states s whose latest touch is c.
        Expected path length = sum over non-start states. For the maximum-likelihood chain this
        equals the empirical mean path length (flow conservation), a property verified in tests.
        """
        model = cls.fit_markov_chain(journeys, channels, order)
        probs = cls._transition_probabilities(model, model.journey_weights)
        t = model.n_transient
        e_start = np.zeros(t)
        e_start[0] = 1.0
        visits = cls._solve((np.eye(t) - probs[:, :t]).T, e_start)
        per_channel = {ch: 0.0 for ch in channels}
        for idx, s in enumerate(model.states[1:], start=1):
            per_channel[s[-1]] = per_channel.get(s[-1], 0.0) + float(visits[idx])
        return per_channel, float(visits[1:].sum())

    @classmethod
    def bootstrap_markov_shares(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str],
        order: int = 1,
        samples: int = 200,
        seed: int = 20261027,
        confidence: float = 0.95
    ) -> dict[str, tuple[float, float]]:
        """
        Non-parametric bootstrap: resample the journey multiset with replacement
        (multinomial over unique journeys), refit the chain, recompute W_k, and take the
        percentile interval [alpha/2, 1 - alpha/2] of each channel's share.
        """
        if samples <= 0 or not journeys:
            return {}
        model = cls.fit_markov_chain(journeys, channels, order)
        rng = np.random.default_rng(seed)
        n = int(model.journey_weights.sum())
        p = model.journey_weights / model.journey_weights.sum()
        draws: dict[str, list[float]] = {ch: [] for ch in channels}
        for _ in range(samples):
            w = rng.multinomial(n, p).astype(np.float64)
            probs = cls._transition_probabilities(model, w)
            effects, _ = cls._removal_effects(model, channels, probs)
            shares = cls._normalize_effects(effects, channels)
            for ch in channels:
                draws[ch].append(shares[ch])
        alpha = (1.0 - confidence) / 2.0
        return {
            ch: (percentile(sorted(v), 100 * alpha), percentile(sorted(v), 100 * (1 - alpha)))
            for ch, v in draws.items()
        }

    # ------------------------------------------------------------------
    # Shapley values
    # ------------------------------------------------------------------
    @staticmethod
    def _converted_coalitions(
        journeys: list[CustomerJourneyInput], channel_set: set[str]
    ) -> Counter:
        """Total conversion value per distinct converted channel set T (the game's unanimity basis)."""
        values: Counter = Counter()
        for j in journeys:
            if j.converted:
                s = frozenset(c for c in j.path if c in channel_set)
                if s:
                    values[s] += j.conversion_value_usd if j.conversion_value_usd > 0.0 else 1.0
        return values

    @classmethod
    def compute_shapley_values(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str],
        method: str = "auto"
    ) -> dict[str, float]:
        """
        Raw (un-normalized) Shapley values phi_k of the game
            v(S) = total conversion value of converted journeys whose channel set is a subset of S.
        'exact' enumerates all 2^N coalitions:
            phi_k = sum_{S in N \\ {k}} |S|! (|N| - |S| - 1)! / |N|! * [v(S u {k}) - v(S)]
        'closed_form' uses the unanimity-game decomposition v = sum_T value(T) * u_T, for which
            phi_k = sum_{T contains k} value(T) / |T|   (exact, O(journeys))
        'auto' enumerates up to MAX_SHAPLEY_CHANNELS and switches to the closed form above it,
        so no channel is ever silently dropped.
        """
        n = len(channels)
        if n == 0:
            return {}
        coalitions = cls._converted_coalitions(journeys, set(channels))

        use_exact = method == "exact" or (method == "auto" and n <= settings.MAX_SHAPLEY_CHANNELS)
        if method == "exact" and n > settings.MAX_SHAPLEY_CHANNELS:
            raise ValueError(f"Exact Shapley enumeration is capped at {settings.MAX_SHAPLEY_CHANNELS} channels.")

        if not use_exact:
            phi = {ch: 0.0 for ch in channels}
            for t_set, value in coalitions.items():
                share = value / len(t_set)
                for ch in t_set:
                    phi[ch] += share
            return phi

        factorial_n = math.factorial(n)
        coalition_values: dict[frozenset[str], float] = {}
        items = list(coalitions.items())
        for r in range(n + 1):
            for comb in itertools.combinations(channels, r):
                s = frozenset(comb)
                coalition_values[s] = sum(v for t_set, v in items if t_set <= s)

        weights = [math.factorial(r) * math.factorial(n - r - 1) / factorial_n for r in range(n)]
        phi = {}
        for ch in channels:
            remaining = [c for c in channels if c != ch]
            ch_set = frozenset([ch])
            total = 0.0
            for r in range(n):
                for comb in itertools.combinations(remaining, r):
                    s = frozenset(comb)
                    total += weights[r] * (coalition_values[s | ch_set] - coalition_values[s])
            phi[ch] = total
        return phi

    @classmethod
    def compute_shapley_attribution(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str]
    ) -> dict[str, float]:
        """
        Normalized Shapley shares (sum to 1.0 within 1e-9).
        Efficiency guarantees sum(phi_k) = v(N) - v(empty) before normalization.
        """
        if not channels:
            return {}
        phi = cls.compute_shapley_values(journeys, channels)
        total = sum(max(0.0, v) for v in phi.values())
        if total > 1e-12:
            return {ch: max(0.0, phi[ch]) / total for ch in channels}
        return {ch: 1.0 / len(channels) for ch in channels}

    # ------------------------------------------------------------------
    # Rule-based models
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_rounded(d: dict[str, float]) -> dict[str, float]:
        total = sum(d.values())
        if total > 0:
            return {k: round(v / total, 6) for k, v in d.items()}
        return {k: 1.0 / len(d) for k in d} if d else {}

    @classmethod
    def compute_heuristic_attributions(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str]
    ) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
        """Computes Last-Click, First-Touch, and Linear attribution shares (conversion-weighted)."""
        last_clicks = {ch: 0.0 for ch in channels}
        first_touches = {ch: 0.0 for ch in channels}
        linear_touches = {ch: 0.0 for ch in channels}

        for j in journeys:
            if not j.converted:
                continue
            valid_path = [c for c in j.path if c in last_clicks]
            if not valid_path:
                continue
            first_touches[valid_path[0]] += 1.0
            last_clicks[valid_path[-1]] += 1.0
            share_per_touch = 1.0 / len(valid_path)
            for ch in valid_path:
                linear_touches[ch] += share_per_touch

        return (cls._normalize_rounded(last_clicks),
                cls._normalize_rounded(first_touches),
                cls._normalize_rounded(linear_touches))

    @staticmethod
    def position_based_weights(length: int, first: float = 0.4, last: float = 0.4) -> list[float]:
        """U-shaped weights: 100% for one touch, 50/50 for two, else first/last get 40% and the middle shares 20%."""
        if length <= 0:
            return []
        if length == 1:
            return [1.0]
        if length == 2:
            return [0.5, 0.5]
        middle = (1.0 - first - last) / (length - 2)
        return [first] + [middle] * (length - 2) + [last]

    @staticmethod
    def time_decay_weights(length: int, half_life: float = 2.0) -> list[float]:
        """w_i = 2^(-(L - 1 - i) / h) normalized: a touch h steps before conversion earns half the credit."""
        if length <= 0:
            return []
        raw = [2.0 ** (-(length - 1 - i) / half_life) for i in range(length)]
        total = sum(raw)
        return [w / total for w in raw]

    @classmethod
    def compute_positional_attributions(
        cls,
        journeys: list[CustomerJourneyInput],
        channels: list[str],
        half_life: float = 2.0
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Returns (position_based_shares, time_decay_shares) over converted journeys."""
        position = {ch: 0.0 for ch in channels}
        decay = {ch: 0.0 for ch in channels}
        for j in journeys:
            if not j.converted:
                continue
            path = [c for c in j.path if c in position]
            for ch, w in zip(path, cls.position_based_weights(len(path))):
                position[ch] += w
            for ch, w in zip(path, cls.time_decay_weights(len(path), half_life)):
                decay[ch] += w
        return cls._normalize_rounded(position), cls._normalize_rounded(decay)

    # ------------------------------------------------------------------
    # End-to-end pipeline
    # ------------------------------------------------------------------
    @classmethod
    def _load_journeys(cls, request: MtaComputeRequest, db: Optional[Session]) -> list[CustomerJourneyInput]:
        if request.journeys:
            return list(request.journeys)
        if db is None:
            return []
        rows = (
            db.query(CustomerJourney)
            .filter(CustomerJourney.tenant_id == request.tenant_id)
            .limit(settings.MAX_JOURNEYS_PER_REQUEST)
            .all()
        )
        journeys = []
        for row in rows:
            path = json.loads(row.path_sequence_json)
            if not isinstance(path, list) or not path:
                continue
            journeys.append(CustomerJourneyInput(
                session_hash=row.session_hash,
                path=[str(c) for c in path][: settings.MAX_PATH_LENGTH],
                converted=row.converted,
                conversion_value_usd=row.conversion_value_usd
            ))
        return journeys

    @classmethod
    def execute_mta_pipeline(
        cls,
        request: MtaComputeRequest,
        db: Optional[Session] = None
    ) -> MtaComputeResponse:
        """
        1. Ingests or queries journeys and spends
        2. Solves order-k Markov removal effects (+ optional bootstrap CI)
        3. Computes Shapley values and five rule-based models
        4. Measures model disagreement (Jensen-Shannon, total variation)
        5. Optimizes budget under diminishing returns and classifies marginal ROAS
        """
        journeys = cls._load_journeys(request, db)
        if not journeys:
            raise ValueError("No customer journeys found for the requested tenant.")

        channel_list = sorted({c for j in journeys for c in j.path})
        if len(channel_list) > settings.MAX_DISTINCT_CHANNELS:
            raise ValueError(f"Too many distinct channels ({len(channel_list)}); limit is {settings.MAX_DISTINCT_CHANNELS}.")

        # Channel spend
        spend_map: dict[str, float] = {ch: 5000.0 for ch in channel_list}
        if request.channel_spends:
            for cs in request.channel_spends:
                if cs.channel_name in spend_map:
                    spend_map[cs.channel_name] = cs.monthly_spend_usd
        elif db is not None:
            for cs in db.query(ChannelSpend).filter(ChannelSpend.tenant_id == request.tenant_id).all():
                if cs.channel_name in spend_map:
                    spend_map[cs.channel_name] = cs.monthly_spend_usd

        total_conversions = sum(1 for j in journeys if j.converted)
        total_revenue = sum(j.conversion_value_usd for j in journeys if j.converted)
        if total_revenue == 0.0 and total_conversions > 0:
            total_revenue = total_conversions * 150.0  # default synthetic AOV

        current_total_spend = sum(spend_map.values())
        budget = request.total_budget_usd or current_total_spend

        # Models
        markov_shares, removal_effects, trans_matrix, base_p_conv = cls.compute_markov_attribution(
            journeys, channel_list, order=request.markov_order
        )
        visits, expected_path_length = cls.markov_expected_visits(journeys, channel_list, order=request.markov_order)
        ci = cls.bootstrap_markov_shares(
            journeys, channel_list, order=request.markov_order, samples=request.bootstrap_samples
        )
        shapley_shares = cls.compute_shapley_attribution(journeys, channel_list)
        last_click, first_touch, linear = cls.compute_heuristic_attributions(journeys, channel_list)
        position_based, time_decay = cls.compute_positional_attributions(
            journeys, channel_list, half_life=request.time_decay_half_life
        )

        divergences = [
            ModelDivergence(
                model=name,
                jensen_shannon_bits=round(jensen_shannon_bits(dist, markov_shares), 6),
                total_variation=round(total_variation(dist, markov_shares), 6),
            )
            for name, dist in (
                ("Last-Click", last_click), ("First-Touch", first_touch), ("Linear", linear),
                ("Position-Based", position_based), ("Time-Decay", time_decay), ("Shapley", shapley_shares),
            )
        ]

        attributed_revenue = {ch: markov_shares[ch] * total_revenue for ch in channel_list}
        optimum = optimize_budget(
            attributed_revenue, spend_map, budget,
            beta=request.response_elasticity, trust_region=request.reallocation_trust_region,
        )
        lam = optimum.equilibrium_marginal_roas

        channels_result: list[ChannelAttributionShare] = []
        for ch in channel_list:
            spend = spend_map[ch]
            m_share = markov_shares[ch]
            attr_conv = m_share * total_conversions
            mroas_now = optimum.marginal_roas_current[ch]
            if spend <= 0.0:
                category = "Unfunded (No Spend Baseline)"
                mroas_now = 0.0
            elif mroas_now > lam * 1.15:
                category = "Under-Invested (High Growth)"
            elif mroas_now < lam * 0.85:
                category = "Over-Invested (Margin Erosion)"
            else:
                category = "Optimized (Equilibrium)"
            ci_pair = ci.get(ch)

            channels_result.append(ChannelAttributionShare(
                channel_name=ch,
                last_click_share=round(last_click[ch], 4),
                first_touch_share=round(first_touch[ch], 4),
                linear_share=round(linear[ch], 4),
                position_based_share=round(position_based[ch], 4),
                time_decay_share=round(time_decay[ch], 4),
                markov_share=round(m_share, 4),
                markov_share_ci_low=round(ci_pair[0], 4) if ci_pair else None,
                markov_share_ci_high=round(ci_pair[1], 4) if ci_pair else None,
                shapley_share=round(shapley_shares[ch], 4),
                markov_removal_effect=round(removal_effects[ch], 4),
                expected_visits_per_journey=round(visits.get(ch, 0.0), 4),
                attributed_conversions_markov=round(attr_conv, 1),
                attributed_revenue_markov=round(attributed_revenue[ch], 2),
                current_spend_usd=round(spend, 2),
                cpa_markov=round(spend / attr_conv, 2) if attr_conv > 0 else None,
                roas_last_click=round((last_click[ch] * total_revenue) / spend, 2) if spend > 0 else 0.0,
                roas_markov=round(attributed_revenue[ch] / spend, 2) if spend > 0 else 0.0,
                roas_shapley=round((shapley_shares[ch] * total_revenue) / spend, 2) if spend > 0 else 0.0,
                marginal_roas_current=round(mroas_now, 4),
                marginal_roas_optimized=round(optimum.marginal_roas_optimized[ch], 4) if spend > 0 else 0.0,
                recommended_budget_reallocation_usd=round(optimum.allocation[ch], 2),
                marginal_roas_category=category
            ))

        channels_result.sort(key=lambda x: x.markov_share, reverse=True)

        if db is not None and request.persist:
            db.query(AttributionResult).filter(AttributionResult.tenant_id == request.tenant_id).delete()
            for item in channels_result:
                db.add(AttributionResult(
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
                ))
            db.commit()

        lc_div = next(d for d in divergences if d.model == "Last-Click")
        top_under = [c for c in channels_result if c.marginal_roas_category.startswith("Under")]
        top_over = sorted(
            (c for c in channels_result if c.marginal_roas_category.startswith("Over")),
            key=lambda c: c.marginal_roas_current,
        )
        insight = (
            f"Last-Click and the order-{request.markov_order} Markov model disagree on "
            f"{lc_div.total_variation * 100:.1f}% of conversion credit (JSD {lc_div.jensen_shannon_bits:.3f} bits). "
            f"Equalizing marginal ROAS at {lam:.2f} moves budget toward "
            f"'{top_under[0].channel_name if top_under else channels_result[0].channel_name}'"
            f"{' and away from ' + repr(top_over[0].channel_name) if top_over else ''}, projecting a "
            f"{optimum.projected_lift_pct:+.1f}% revenue change under diminishing returns (beta={request.response_elasticity})."
        )

        return MtaComputeResponse(
            tenant_id=request.tenant_id,
            total_journeys=len(journeys),
            total_conversions=total_conversions,
            baseline_conversion_rate=round(base_p_conv, 4),
            empirical_conversion_rate=round(total_conversions / len(journeys), 4),
            expected_path_length=round(expected_path_length, 4),
            markov_order=request.markov_order,
            total_revenue_usd=round(total_revenue, 2),
            total_current_spend_usd=round(current_total_spend, 2),
            total_spend_usd=round(budget, 2),
            blended_roas=round(total_revenue / current_total_spend, 2) if current_total_spend > 0 else 0.0,
            projected_revenue_current_usd=round(optimum.projected_revenue_current, 2),
            projected_revenue_optimized_usd=round(optimum.projected_revenue_optimized, 2),
            projected_revenue_lift_pct=round(optimum.projected_lift_pct, 3),
            equilibrium_marginal_roas=round(lam, 4),
            model_divergence_vs_markov=divergences,
            channels=channels_result,
            transition_matrix=trans_matrix,
            model_reallocation_insight=insight
        )

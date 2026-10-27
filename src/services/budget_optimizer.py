"""
Concave budget optimizer with diminishing returns (marginal-ROAS equalization).

Response model per channel k (power-law / constant-elasticity curve):
    revenue_k(x) = a_k * x^beta,   0 < beta < 1
calibrated so that the curve passes through today's attributed revenue at today's spend:
    a_k = attributed_revenue_k / spend_k^beta

Problem:
    maximize  sum_k a_k x_k^beta
    s.t.      sum_k x_k = B,   L_k <= x_k <= U_k
with trust-region bounds L_k = max(0, 1 - d) * s_k * B / S and U_k = (1 + d) * s_k * B / S.

The objective is strictly concave, so the KKT conditions give the unique global optimum:
    mROAS_k(x) = d revenue_k / dx = beta * a_k * x^(beta - 1)
    x_k*(lambda) = clip( (beta * a_k / lambda)^(1 / (1 - beta)), L_k, U_k )
and lambda* (the equilibrium marginal ROAS) solves sum_k x_k*(lambda) = B by bisection.
Without binding bounds this reduces to the closed form x_k* = B * a_k^(1/(1-beta)) / sum_j a_j^(1/(1-beta)).
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field


@dataclass
class BudgetOptimizationResult:
    allocation: dict[str, float]
    marginal_roas_current: dict[str, float]
    marginal_roas_optimized: dict[str, float]
    equilibrium_marginal_roas: float
    projected_revenue_current: float
    projected_revenue_optimized: float
    lower_bounds: dict[str, float] = field(default_factory=dict)
    upper_bounds: dict[str, float] = field(default_factory=dict)

    @property
    def projected_lift_pct(self) -> float:
        if self.projected_revenue_current <= 0.0:
            return 0.0
        return 100.0 * (self.projected_revenue_optimized / self.projected_revenue_current - 1.0)


def response_revenue(a: float, x: float, beta: float) -> float:
    return a * (x ** beta) if x > 0.0 else 0.0


def marginal_roas(a: float, x: float, beta: float) -> float:
    if a <= 0.0:
        return 0.0
    if x <= 0.0:
        return math.inf
    return beta * a * (x ** (beta - 1.0))


def optimize_budget(
    attributed_revenue: dict[str, float],
    current_spend: dict[str, float],
    total_budget: float,
    beta: float = 0.5,
    trust_region: float = 0.5,
    tol: float = 1e-10,
) -> BudgetOptimizationResult:
    if not 0.0 < beta < 1.0:
        raise ValueError("beta must lie strictly between 0 and 1 for a concave response curve.")
    if trust_region < 0.0:
        raise ValueError("trust_region must be non-negative.")

    channels = sorted(current_spend)
    spend_total = sum(max(0.0, current_spend[c]) for c in channels)
    if not channels or total_budget <= 0.0:
        empty = {c: 0.0 for c in channels}
        return BudgetOptimizationResult(empty, empty, empty, 0.0, 0.0, 0.0, empty, empty)

    if spend_total <= 0.0:
        # No spend history to calibrate against: split evenly.
        even = {c: total_budget / len(channels) for c in channels}
        zeros = {c: 0.0 for c in channels}
        return BudgetOptimizationResult(even, zeros, zeros, 0.0, 0.0, 0.0, even, even)

    scale = total_budget / spend_total
    a: dict[str, float] = {}
    lo: dict[str, float] = {}
    hi: dict[str, float] = {}
    for c in channels:
        s = max(0.0, current_spend[c])
        rev = max(0.0, attributed_revenue.get(c, 0.0))
        a[c] = rev / (s ** beta) if s > 0.0 else 0.0
        lo[c] = max(0.0, 1.0 - trust_region) * s * scale
        hi[c] = (1.0 + trust_region) * s * scale

    exponent = 1.0 / (1.0 - beta)

    def alloc(lam: float) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in channels:
            if a[c] <= 0.0:
                out[c] = lo[c]
                continue
            unconstrained = (beta * a[c] / lam) ** exponent
            out[c] = min(hi[c], max(lo[c], unconstrained))
        return out

    # sum(alloc(lambda)) is non-increasing in lambda; bisect in log-space.
    log_lo, log_hi = math.log(1e-12), math.log(1e12)
    for _ in range(300):
        mid = 0.5 * (log_lo + log_hi)
        if sum(alloc(math.exp(mid)).values()) > total_budget:
            log_lo = mid
        else:
            log_hi = mid
        if log_hi - log_lo < tol:
            break
    lam_star = math.exp(0.5 * (log_lo + log_hi))
    allocation = alloc(lam_star)

    # Remove the residual bisection error from channels strictly inside their bounds.
    residual = total_budget - sum(allocation.values())
    free = [c for c in channels if lo[c] + 1e-9 < allocation[c] < hi[c] - 1e-9]
    if free and abs(residual) > 0.0:
        free_total = sum(allocation[c] for c in free)
        for c in free:
            allocation[c] += residual * (allocation[c] / free_total if free_total > 0 else 1.0 / len(free))

    projected_current = sum(response_revenue(a[c], max(0.0, current_spend[c]) * scale, beta) for c in channels)
    projected_optimized = sum(response_revenue(a[c], allocation[c], beta) for c in channels)

    return BudgetOptimizationResult(
        allocation=allocation,
        marginal_roas_current={c: marginal_roas(a[c], max(0.0, current_spend[c]), beta) for c in channels},
        marginal_roas_optimized={c: marginal_roas(a[c], allocation[c], beta) for c in channels},
        equilibrium_marginal_roas=lam_star,
        projected_revenue_current=projected_current,
        projected_revenue_optimized=projected_optimized,
        lower_bounds=lo,
        upper_bounds=hi,
    )

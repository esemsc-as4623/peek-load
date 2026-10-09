"""Hard spending cap for the Claude API.

The cache ledger records the cost of every API response. Before a stage is submitted, `check` projects its
cost from per-call costs measured on a small calibration run of the same configuration, and refuses
(or tells the caller how many requests still fit) if the projection would push total spend past the cap in
config/params.yaml. Projections carry a safety margin because thinking-token counts vary between calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from rtl.llm.cache import Cache
from rtl.settings import params

MARGIN = 1.15  # projected cost is inflated by 15% before comparing with the remaining budget


@dataclass
class BudgetCheck:
    spent: float
    cap: float
    projected: float
    affordable_n: int  # how many requests of this kind still fit

    @property
    def ok(self) -> bool:
        return self.spent + self.projected <= self.cap


def spent(cache: Cache | None = None) -> float:
    led = (cache or Cache()).ledger()
    return float(led.cost_usd.sum()) if len(led) else 0.0


def check(n_requests: int, usd_per_request: float, cache: Cache | None = None, cap: float | None = None) -> BudgetCheck:
    cap = float(cap if cap is not None else params()["llm"]["budget_usd"])
    s = spent(cache)
    per = usd_per_request * MARGIN
    projected = n_requests * per
    affordable = int(max(0.0, cap - s) // per) if per > 0 else n_requests
    return BudgetCheck(s, cap, projected, min(affordable, n_requests))


def require(n_requests: int, usd_per_request: float, what: str, cache: Cache | None = None) -> None:
    """Raise if a stage doesn't fit; print the numbers either way."""
    b = check(n_requests, usd_per_request, cache)
    print(f"[budget] {what}: {n_requests} req x ${usd_per_request:.4f} (+{MARGIN - 1:.0%}) = ${b.projected:.2f}; "
          f"spent ${b.spent:.2f} of ${b.cap:.0f}")
    if not b.ok:
        raise SystemExit(f"[budget] refused: only {b.affordable_n} of {n_requests} requests fit under the cap")

"""Deterministic market simulator for the re-quoting brain — pure, no I/O.

Drives ``plan_requote`` tick-by-tick through a scripted book + taker arrivals and
applies a simple fill model, so tests can prove end-to-end re-quoting behaviour
(catches pairs, respects caps) WITHOUT any network or live trading.

Fill model: ``taker="YES"`` means a seller hits our resting YES bid this tick →
we BUY that resting size at our bid price. Same for ``"NO"``. ``None`` = no taker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.requote_planner import RestingOrder, plan_requote


@dataclass
class RequoteSim:
    cfg: Config
    inv_yes: int = 0
    inv_no: int = 0
    yes_cost: float = 0.0
    no_cost: float = 0.0
    resting: dict[str, RestingOrder | None] = field(
        default_factory=lambda: {"YES": None, "NO": None})
    _oid: int = 0
    max_naked: int = 0

    @property
    def naked(self) -> int:
        return abs(self.inv_yes - self.inv_no)

    @property
    def matched(self) -> int:
        return min(self.inv_yes, self.inv_no)

    @property
    def spent(self) -> float:
        return self.yes_cost + self.no_cost

    def avg_pair_cost(self) -> float | None:
        if self.inv_yes == 0 or self.inv_no == 0:
            return None
        return self.yes_cost / self.inv_yes + self.no_cost / self.inv_no

    def tick(self, yes_bid: float, no_bid: float, taker: str | None = None) -> None:
        plan = plan_requote(
            yes_bid=yes_bid, no_bid=no_bid,
            inv_yes=self.inv_yes, inv_no=self.inv_no,
            yes_cost=self.yes_cost, no_cost=self.no_cost,
            resting=self.resting, cfg=self.cfg,
        )
        # 1) cancels
        for cid in plan.cancels:
            for side, ro in self.resting.items():
                if ro is not None and ro.order_id == cid:
                    self.resting[side] = None
        # 2) posts
        for q in plan.posts:
            self._oid += 1
            self.resting[q.side] = RestingOrder(f"o{self._oid}", q.side, q.price, q.size)
        # 3) taker fill (a seller hits our resting bid on that side)
        if taker in ("YES", "NO"):
            ro = self.resting[taker]
            if ro is not None:
                if taker == "YES":
                    self.inv_yes += ro.size
                    self.yes_cost += ro.price * ro.size
                else:
                    self.inv_no += ro.size
                    self.no_cost += ro.price * ro.size
                self.resting[taker] = None
        # 4) track worst naked
        self.max_naked = max(self.max_naked, self.naked)

    def run(self, script: list[tuple]) -> None:
        """script: list of (yes_bid, no_bid, taker) tuples."""
        for yes_bid, no_bid, taker in script:
            self.tick(yes_bid, no_bid, taker)

"""Deterministic market simulator for the re-quoting brain — pure, no I/O.

Drives ``plan_requote`` tick-by-tick through a scripted book + taker arrivals and
applies a simple fill model, so tests can prove end-to-end re-quoting behaviour
(catches cheap pairs, never completes a pair >= $1, respects caps/targets) WITHOUT
any network or live trading.

Fill model: ``taker="YES"`` means a seller hits our resting YES bid this tick →
we BUY that resting size at our bid price. Same for ``"NO"``. ``None`` = no taker.
Book ticks are ``(yes_bid, no_bid, taker)`` or ``(yes_bid, no_bid, taker, yes_ask, no_ask)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.requote_planner import RestingOrder, plan_requote


@dataclass
class RequoteSim:
    cfg: Config
    target_shares: int = 0   # 0 → default to cfg.flat_size
    inv_yes: int = 0
    inv_no: int = 0
    yes_cost: float = 0.0
    no_cost: float = 0.0
    resting: dict[str, RestingOrder | None] = field(
        default_factory=lambda: {"YES": None, "NO": None})
    _oid: int = 0
    max_naked: int = 0

    def __post_init__(self):
        if self.target_shares <= 0:
            self.target_shares = self.cfg.flat_size

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

    def tick(self, yes_bid, no_bid, taker=None, yes_ask=None, no_ask=None) -> None:
        if yes_ask is None:
            yes_ask = round(yes_bid + 0.01, 2)
        if no_ask is None:
            no_ask = round(no_bid + 0.01, 2)
        resting_val = sum(ro.price * ro.size for ro in self.resting.values() if ro is not None)
        committed = self.spent + resting_val

        plan = plan_requote(
            yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
            inv_yes=self.inv_yes, inv_no=self.inv_no,
            yes_cost=self.yes_cost, no_cost=self.no_cost,
            committed=committed, target_shares=self.target_shares,
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
        # 3) taker fill
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
        for t in script:
            self.tick(*t)

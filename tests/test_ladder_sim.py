"""Offline ladder simulator: drives plan_ladder with scripted taker fills, applies them
to LocalInventory, and checks the invariants (naked bounded, cheap pairs form)."""

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.ladder_planner import plan_ladder
from quoter.runner.local_inventory import LocalInventory
from quoter.runner.requote_planner import RestingOrder


def cfg(**kw):
    base = dict(merge_edge=0.02, ladder_anchor="entry", rungs=5, rung_size=5,
                rung_spacing=0.03, naked_cap=10, per_window_cap=25.0)
    base.update(kw)
    return Config(**base)


@dataclass
class LadderSim:
    cfg: Config
    entry_mid: float
    local: LocalInventory = field(default_factory=LocalInventory)
    resting: dict = field(default_factory=lambda: {"YES": [], "NO": []})
    _oid: int = 0
    max_naked: int = 0

    def _committed(self):
        rest = sum(ro.price * ro.size for s in ("YES", "NO") for ro in self.resting[s])
        return self.local.cost["YES"] + self.local.cost["NO"] + rest

    def tick(self, yes_bid, no_bid, fills=None, yes_ask=0.99, no_ask=0.99):
        plan = plan_ladder(
            yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
            entry_mid=self.entry_mid, inv_yes=self.local.inv["YES"],
            inv_no=self.local.inv["NO"], yes_cost=self.local.cost["YES"],
            no_cost=self.local.cost["NO"], committed=self._committed(),
            resting=self.resting, cfg=self.cfg)
        for cid in plan.cancels:
            for s in ("YES", "NO"):
                self.resting[s] = [ro for ro in self.resting[s] if ro.order_id != cid]
        for q in plan.posts:
            self._oid += 1
            self.resting[q.side].append(RestingOrder(f"o{self._oid}", q.side, q.price, q.size))
        for side, price in (fills or []):
            for ro in list(self.resting[side]):
                if abs(ro.price - price) < 1e-9:
                    self.local.credit_fill(side, ro.size, ro.price)
                    self.resting[side].remove(ro)
                    break
        self.max_naked = max(self.max_naked,
                             abs(self.local.inv["YES"] - self.local.inv["NO"]))


def test_naked_bounded_on_one_sided_dump():
    c = cfg()
    sim = LadderSim(cfg=c, entry_mid=0.45)
    sim.tick(0.44, 0.54)
    for p in [0.54, 0.51, 0.48, 0.45, 0.42]:
        sim.tick(0.44, p, fills=[("NO", p)])
    assert sim.max_naked <= c.naked_cap + c.rung_size
    assert sim.local.inv["NO"] <= c.naked_cap + c.rung_size
    assert sim.local.inv["NO"] >= c.rung_size          # non-vacuous: the ladder actually filled
    assert sim.local.inv["NO"] >= c.naked_cap          # accumulated up to ~the cap before rungs were pulled


def test_cheap_pair_forms_on_two_sided_dips():
    # A 4-rung-deep dip (0.35 / 0.45) can only be caught by a ladder that rests that
    # deep — which the lag-proof cap allows only when naked_cap covers the full depth
    # (naked_cap // rung_size >= rungs). With a tight cap the ladder is intentionally
    # shallow (the documented deep-ladder ↔ small-cap trade-off), so use a wide cap here.
    c = cfg(naked_cap=25)
    sim = LadderSim(cfg=c, entry_mid=0.45)
    sim.tick(0.44, 0.54)
    sim.tick(0.44, 0.54, fills=[("YES", 0.35), ("NO", 0.45)])
    iy, ino = sim.local.inv["YES"], sim.local.inv["NO"]
    assert iy > 0 and ino > 0
    pair_cost = sim.local.cost["YES"] / iy + sim.local.cost["NO"] / ino
    assert pair_cost < 1.0

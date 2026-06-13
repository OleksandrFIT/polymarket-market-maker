"""Offline ladder simulator: drives plan_ladder with scripted taker fills, rebuilds
inventory from the real-fill list via inventory_from_fills, and drives plan_naked_action
(COMPLETE buys the light side, SELL sells the heavy side + suppresses it). Checks the
invariants (naked bounded, cheap pairs form, complete-or-sell)."""

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.fill_inventory import inventory_from_fills
from quoter.runner.flatten_planner import plan_naked_action
from quoter.runner.ladder_planner import plan_ladder
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
    resting: dict = field(default_factory=lambda: {"YES": [], "NO": []})
    fills: list = field(default_factory=list)   # real-fill list (ground truth)
    _oid: int = 0
    max_naked: int = 0
    auto_flat: bool = False
    grace_ticks: int = 0
    flattened: set = field(default_factory=set)
    flatten_count: int = 0
    complete_count: int = 0
    _tick: int = 0
    _naked_since: dict = field(default_factory=lambda: {"YES": None, "NO": None})

    def _inv(self):
        return inventory_from_fills(self.fills)

    def _committed(self, inv):
        rest = sum(ro.price * ro.size for s in ("YES", "NO") for ro in self.resting[s])
        return inv.cost["YES"] + inv.cost["NO"] + rest

    def tick(self, yes_bid, no_bid, fills=None, yes_ask=0.99, no_ask=0.99):
        self._tick += 1
        inv = self._inv()
        iy, ino = inv.inv["YES"], inv.inv["NO"]
        # --- naked-action gate (mirrors merge_runner._ladder_window) ---
        if self.auto_flat:
            naked = iy - ino
            heavy = "YES" if naked > 0 else ("NO" if naked < 0 else None)
            for s in ("YES", "NO"):
                if s != heavy:
                    self._naked_since[s] = None
            if heavy and abs(naked) >= self.cfg.naked_cap and heavy not in self.flattened:
                if self._naked_since[heavy] is None:
                    self._naked_since[heavy] = self._tick
                elif self._tick - self._naked_since[heavy] >= self.grace_ticks:
                    a = plan_naked_action(iy, ino, inv.avg("YES"), inv.avg("NO"),
                                          yes_ask, no_ask, self.cfg.naked_cap)
                    if a and a.kind == "COMPLETE":
                        ask = yes_ask if a.side == "YES" else no_ask
                        self.fills.append({"side": a.side, "action": "BUY",
                                           "size": a.qty, "price": ask})
                        self.complete_count += 1
                        self._naked_since[heavy] = None
                    elif a and a.kind == "SELL":
                        bid = yes_bid if a.side == "YES" else no_bid
                        self.fills.append({"side": a.side, "action": "SELL",
                                           "size": a.qty, "price": bid})
                        self.flattened.add(a.side)
                        self.flatten_count += 1
            elif heavy and abs(naked) < self.cfg.naked_cap:
                self._naked_since[heavy] = None
        # re-read inventory after any action
        inv = self._inv()
        plan = plan_ladder(
            yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
            entry_mid=self.entry_mid, inv_yes=inv.inv["YES"], inv_no=inv.inv["NO"],
            yes_cost=inv.cost["YES"], no_cost=inv.cost["NO"],
            committed=self._committed(inv), resting=self.resting, cfg=self.cfg,
            suppressed=frozenset(self.flattened))
        for cid in plan.cancels:
            for s in ("YES", "NO"):
                self.resting[s] = [ro for ro in self.resting[s] if ro.order_id != cid]
        for q in plan.posts:
            self._oid += 1
            self.resting[q.side].append(RestingOrder(f"o{self._oid}", q.side, q.price, q.size))
        # scripted taker fills hit our resting bids -> we BUY
        for side, price in (fills or []):
            for ro in list(self.resting[side]):
                if abs(ro.price - price) < 1e-9:
                    self.fills.append({"side": side, "action": "BUY",
                                       "size": ro.size, "price": ro.price})
                    self.resting[side].remove(ro)
                    break
        inv = self._inv()
        self.max_naked = max(self.max_naked, abs(inv.inv["YES"] - inv.inv["NO"]))


def _inv(sim):
    return sim._inv().inv


def test_naked_bounded_on_one_sided_dump():
    c = cfg()
    sim = LadderSim(cfg=c, entry_mid=0.45)
    sim.tick(0.44, 0.54)
    for p in [0.54, 0.51, 0.48, 0.45, 0.42]:
        sim.tick(0.44, p, fills=[("NO", p)])
    assert sim.max_naked <= c.naked_cap + c.rung_size
    assert _inv(sim)["NO"] <= c.naked_cap + c.rung_size
    assert _inv(sim)["NO"] >= c.rung_size          # non-vacuous: the ladder actually filled


def test_cheap_pair_forms_on_two_sided_dips():
    c = cfg(naked_cap=25)
    sim = LadderSim(cfg=c, entry_mid=0.45)
    sim.tick(0.44, 0.54)
    sim.tick(0.44, 0.54, fills=[("YES", 0.35), ("NO", 0.45)])
    iy, ino = _inv(sim)["YES"], _inv(sim)["NO"]
    assert iy > 0 and ino > 0
    pair_cost = sim._inv().cost["YES"] / iy + sim._inv().cost["NO"] / ino
    assert pair_cost < 1.0


def test_naked_completes_into_pair_when_other_side_cheap():
    # window-3 scenario: naked YES, the NO ask is cheap -> COMPLETE (buy NO), no SELL.
    c = cfg(naked_cap=5, auto_flat=True)
    sim = LadderSim(cfg=c, entry_mid=0.50, auto_flat=True, grace_ticks=1)
    # taker hits our resting YES rung (entry-anchored @ 0.49) -> 5 naked YES, avg 0.49
    sim.tick(0.61, 0.39)
    sim.tick(0.61, 0.39, fills=[("YES", 0.49)])   # naked 5 YES appears
    # NO ask cheap (0.36): 0.49 + 0.36 = 0.85 < 1 -> COMPLETE buys NO
    sim.tick(0.61, 0.36, no_ask=0.36)             # naked first seen by gate -> arm grace clock
    sim.tick(0.61, 0.36, no_ask=0.36)             # grace elapses -> COMPLETE buys NO
    inv = sim._inv()
    assert sim.complete_count >= 1
    assert sim.flatten_count == 0
    assert "YES" not in sim.flattened              # COMPLETE does NOT suppress
    assert inv.inv["YES"] == inv.inv["NO"]         # balanced into a pair


def test_naked_sells_when_other_side_too_expensive():
    # trend scenario: naked NO, YES ask too expensive to complete -> SELL + suppress NO.
    c = cfg(naked_cap=5, auto_flat=True)
    sim = LadderSim(cfg=c, entry_mid=0.50, auto_flat=True, grace_ticks=1)
    sim.tick(0.40, 0.60)
    # taker hits our resting NO rung (entry-anchored @ 0.49) -> 5 naked NO, avg 0.49
    sim.tick(0.40, 0.60, fills=[("NO", 0.49)])    # naked 5 NO appears
    # YES ask too expensive: 0.49 + 0.55 = 1.04 >= 1 -> SELL NO
    sim.tick(0.40, 0.60, yes_ask=0.55)            # naked first seen by gate -> arm grace clock
    sim.tick(0.40, 0.60, yes_ask=0.55)            # grace elapses -> SELL NO + suppress
    assert sim.flatten_count >= 1
    assert sim.complete_count == 0
    assert "NO" in sim.flattened                   # SELL suppresses
    assert abs(sim._inv().inv["YES"] - sim._inv().inv["NO"]) < c.naked_cap

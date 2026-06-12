"""Offline ladder simulator: drives plan_ladder with scripted taker fills, applies them
to LocalInventory, and checks the invariants (naked bounded, cheap pairs form)."""

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.flatten_planner import plan_flatten
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
    auto_flat: bool = False
    grace_ticks: int = 0
    flattened: set = field(default_factory=set)
    flatten_count: int = 0
    _tick: int = 0
    _naked_since: dict = field(default_factory=lambda: {"YES": None, "NO": None})

    def _committed(self):
        rest = sum(ro.price * ro.size for s in ("YES", "NO") for ro in self.resting[s])
        return self.local.cost["YES"] + self.local.cost["NO"] + rest

    def tick(self, yes_bid, no_bid, fills=None, yes_ask=0.99, no_ask=0.99):
        self._tick += 1
        # --- auto-flat gate (mirrors merge_runner._ladder_window) ---
        if self.auto_flat:
            naked = self.local.inv["YES"] - self.local.inv["NO"]
            heavy = "YES" if naked > 0 else ("NO" if naked < 0 else None)
            for s in ("YES", "NO"):
                if s != heavy:
                    self._naked_since[s] = None
            if heavy and abs(naked) >= self.cfg.naked_cap and heavy not in self.flattened:
                if self._naked_since[heavy] is None:
                    self._naked_since[heavy] = self._tick
                elif self._tick - self._naked_since[heavy] >= self.grace_ticks:
                    dec = plan_flatten(self.local.inv["YES"], self.local.inv["NO"],
                                       self.cfg.naked_cap)
                    if dec:
                        bid = yes_bid if dec.side == "YES" else no_bid
                        self.local.debit_fill(dec.side, dec.qty, bid)
                        self.flattened.add(dec.side)
                        self.flatten_count += 1
            elif heavy and abs(naked) < self.cfg.naked_cap:
                self._naked_since[heavy] = None
        # --- plan + apply (existing behaviour, now with suppressed) ---
        plan = plan_ladder(
            yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
            entry_mid=self.entry_mid, inv_yes=self.local.inv["YES"],
            inv_no=self.local.inv["NO"], yes_cost=self.local.cost["YES"],
            no_cost=self.local.cost["NO"], committed=self._committed(),
            resting=self.resting, cfg=self.cfg,
            suppressed=frozenset(self.flattened))
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


def test_auto_flat_kills_persistent_naked_and_suppresses():
    # Sustained one-sided DOWN dump: only Down fills. naked_cap=5 bounds it at 5;
    # once it persists past the grace the sim flattens (debit) and suppresses NO.
    c = cfg(naked_cap=5, auto_flat=True, flatten_grace_sec=4.0)
    sim = LadderSim(cfg=c, entry_mid=0.45, auto_flat=True, grace_ticks=2)
    sim.tick(0.44, 0.54)  # post the first rungs
    for p in [0.54, 0.51, 0.48, 0.45, 0.42]:
        sim.tick(0.44, p, fills=[("NO", p)])
    assert sim.flatten_count >= 1                 # a flatten actually happened
    assert "NO" in sim.flattened                  # the heavy side was suppressed
    assert abs(sim.local.inv["YES"] - sim.local.inv["NO"]) < c.naked_cap  # naked killed

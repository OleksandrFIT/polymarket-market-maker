"""Lag-modeling simulator: proves naked stays bounded under a fast dump even when the
real-fills feed lags, and that phantom credits self-correct. Reproduces the live
window-1781365800 sweep (naked 25) in 'lagged' mode; bounds it in 'fixed' mode."""

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.local_inventory import LocalInventory
from quoter.runner.fill_inventory import inventory_from_fills
from quoter.runner.ladder_planner import plan_ladder
from quoter.runner.requote_planner import RestingOrder


def cfg(**kw):
    base = dict(merge_edge=0.02, ladder_anchor="entry", rungs=2, rung_size=5,
                rung_spacing=0.03, naked_cap=5, per_window_cap=99.0, max_inflight_rungs=1)
    base.update(kw)
    return Config(**base)


@dataclass
class LagSim:
    cfg: Config
    entry_mid: float
    mode: str = "fixed"          # "lagged" (buggy) | "fixed" (optimistic+reconcile)
    feed_lag: int = 4            # ticks before a real fill is visible in the feed
    grace_ticks: float = 6.0
    resting: dict = field(default_factory=lambda: {"YES": [], "NO": []})
    local: LocalInventory = field(default_factory=LocalInventory)   # optimistic (fixed)
    real_fills: list = field(default_factory=list)                  # delayed feed (visible)
    _pending: list = field(default_factory=list)                    # (visible_tick, fill)
    _true: LocalInventory = field(default_factory=LocalInventory)   # reality (max_naked)
    _oid: int = 0
    _tick: int = 0
    max_naked: int = 0

    def _real_inv(self):
        return inventory_from_fills(self.real_fills)

    def _post_inv(self):
        return self._real_inv() if self.mode == "lagged" else self.local

    def tick(self, yes_bid, no_bid, yes_ask=0.99, no_ask=0.99,
             dump_side=None, phantom_side=None):
        self._tick += 1
        # 1. promote due delayed fills into the visible feed
        for vt, f in list(self._pending):
            if vt <= self._tick:
                self.real_fills.append(f)
                self._pending.remove((vt, f))
        # 2. fixed mode: reconcile optimistic local with the real feed
        if self.mode == "fixed":
            real = self._real_inv()
            for s in ("YES", "NO"):
                self.local.reconcile_up(s, real.inv[s], 0.0)
                self.local.reconcile_down(s, real.inv[s], float(self._tick), self.grace_ticks)
        # 3. plan from the posting inventory
        pinv = self._post_inv()
        plan = plan_ladder(
            yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
            entry_mid=self.entry_mid, inv_yes=pinv.inv["YES"], inv_no=pinv.inv["NO"],
            yes_cost=pinv.cost["YES"], no_cost=pinv.cost["NO"], committed=0.0,
            resting=self.resting, cfg=self.cfg)
        for cid in plan.cancels:
            for s in ("YES", "NO"):
                self.resting[s] = [ro for ro in self.resting[s] if ro.order_id != cid]
        for q in plan.posts:
            self._oid += 1
            self.resting[q.side].append(RestingOrder(f"o{self._oid}", q.side, q.price, q.size))
        # 4. dump: every resting order on dump_side fills this tick
        if dump_side:
            for ro in list(self.resting[dump_side]):
                self.resting[dump_side].remove(ro)                       # vanish (gone from book)
                self._true.credit_fill(dump_side, ro.size, ro.price)     # reality
                if self.mode == "fixed":
                    self.local.credit_fill(dump_side, ro.size, ro.price) # immediate optimistic
                self._pending.append((self._tick + self.feed_lag,
                                      {"side": dump_side, "action": "BUY",
                                       "size": ro.size, "price": ro.price}))  # delayed feed
        # 5. phantom: a posted order vanishes but NEVER reaches the real feed
        if phantom_side:
            for ro in list(self.resting[phantom_side]):
                self.resting[phantom_side].remove(ro)
                if self.mode == "fixed":
                    self.local.credit_fill(phantom_side, ro.size, ro.price)  # optimistic (wrong)
                # NOTE: not added to _true and not scheduled into the feed -> phantom
                break
        # 6. max naked from REALITY
        self.max_naked = max(self.max_naked,
                             abs(self._true.inv["YES"] - self._true.inv["NO"]))


def test_dump_sweep_reproduced_in_lagged_mode():
    # current behaviour: posting inventory = delayed real feed -> under a fast dump the
    # bot keeps re-posting the NO rung -> naked sweeps far past the cap.
    c = cfg(naked_cap=5)
    sim = LagSim(cfg=c, entry_mid=0.50, mode="lagged", feed_lag=4)
    for _ in range(12):
        sim.tick(0.49, 0.49, dump_side="NO")
    assert sim.max_naked > c.naked_cap + c.rung_size   # sweep reproduced (>10)


def test_dump_sweep_bounded_in_fixed_mode():
    # the fix: optimistic credit-on-vanish bounds posting immediately, regardless of feed lag.
    c = cfg(naked_cap=5)
    sim = LagSim(cfg=c, entry_mid=0.50, mode="fixed", feed_lag=4)
    for _ in range(12):
        sim.tick(0.49, 0.49, dump_side="NO")
    assert sim.max_naked <= c.naked_cap + c.rung_size   # bounded


def test_phantom_corrected_after_grace():
    # a phantom NO credit (vanished but never filled) is lowered to real (0) after grace,
    # so the optimistic local converges back to the truth.
    c = cfg(naked_cap=5)
    sim = LagSim(cfg=c, entry_mid=0.50, mode="fixed", feed_lag=4, grace_ticks=6.0)
    sim.tick(0.49, 0.49, phantom_side="NO")            # injects a phantom NO credit
    assert sim.local.inv["NO"] >= 5                     # optimistic believed it (briefly)
    for _ in range(8):                                  # let grace elapse
        sim.tick(0.49, 0.49)
    assert sim.local.inv["NO"] == 0                     # corrected to real (no real NO fill)

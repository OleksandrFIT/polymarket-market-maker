"""Read-lag simulation: reproduces the live over-buy (15 Up / naked 9) and proves
the LocalInventory fix caps it — the gap the synchronous RequoteSim never modelled.

The synchronous ``RequoteSim`` updates inventory instantly each tick, so its
caps always hold. Live, the on-chain share read LAGS our fills by a few seconds
while the loop runs every ~2s; reading stale inventory, the loop re-posted a
side it had already filled. ``LaggedRequoteSim`` models that lag with two
inventory sources:

  * mode="chain" — the OLD behaviour: feed the planner the lagging chain read,
    drop a vanished order without crediting the fill. Reproduces the over-buy.
  * mode="local" — the FIX: credit a fill the instant our order vanishes
    uncancelled (LocalInventory), reconcile the chain read only upward. Caps it.

All offline — no network, no live trading.
"""

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.local_inventory import LocalInventory
from quoter.runner.requote_planner import RestingOrder, plan_requote


def cfg(**kw):
    base = dict(merge_edge=0.01, max_naked_shares=5, merge_levels=1,
                flat_size=5, per_market_cap_usd=6.0, min_time_to_expiry_sec=5.0)
    base.update(kw)
    return Config(**base)


@dataclass
class LaggedRequoteSim:
    """Drives plan_requote through a scripted book with a TAKER that hits our
    bid, but the chain inventory read trails real fills by ``read_lag`` ticks."""

    cfg: Config
    target_shares: int
    read_lag: int = 3          # ticks before a fill shows in the chain read
    mode: str = "local"        # "local" = fix, "chain" = old buggy behaviour

    resting: dict = field(default_factory=lambda: {"YES": None, "NO": None})
    placed_tick: dict = field(default_factory=lambda: {"YES": -99, "NO": -99})
    last_px: dict = field(default_factory=lambda: {"YES": 0.0, "NO": 0.0})

    real_inv: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})
    real_cost: dict = field(default_factory=lambda: {"YES": 0.0, "NO": 0.0})
    filled_ids: set = field(default_factory=set)
    chain_hist: list = field(default_factory=list)

    local: LocalInventory = field(default_factory=LocalInventory)
    chain_prev: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})
    chain_cost: dict = field(default_factory=lambda: {"YES": 0.0, "NO": 0.0})

    _oid: int = 0
    _t: int = 0
    max_real_naked: int = 0
    max_real_inv: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})

    def _chain_read(self) -> dict:
        idx = self._t - self.read_lag
        return self.chain_hist[idx] if 0 <= idx < len(self.chain_hist) else {"YES": 0, "NO": 0}

    def _open_ids(self) -> set:
        return {ro.order_id for ro in self.resting.values()
                if ro is not None and ro.order_id not in self.filled_ids}

    def tick(self, yes_bid, no_bid, taker=None, yes_ask=None, no_ask=None) -> None:
        t = self._t
        if yes_ask is None:
            yes_ask = round(yes_bid + 0.01, 2)
        if no_ask is None:
            no_ask = round(no_bid + 0.01, 2)
        chain = self._chain_read()
        open_ids = self._open_ids()

        # --- inventory accounting (the code under test) ---
        for side, bid in (("YES", yes_bid), ("NO", no_bid)):
            ro = self.resting[side]
            vanished = ro is not None and ro.order_id not in open_ids and (t - self.placed_tick[side]) >= 1
            if self.mode == "local":
                if vanished:                       # vanished uncancelled => filled
                    self.local.credit_fill(side, ro.size, ro.price)
                    self.resting[side] = None
                self.local.reconcile_up(side, chain[side], self.last_px[side] or bid)
            else:                                   # "chain": old buggy behaviour
                if vanished:
                    self.resting[side] = None        # dropped, fill NOT credited
                d = chain[side] - self.chain_prev[side]
                if d > 0:
                    self.chain_cost[side] += d * (self.last_px[side] or bid)
                self.chain_prev[side] = chain[side]

        if self.mode == "local":
            inv = dict(self.local.inv)
            cost = dict(self.local.cost)
        else:
            inv = dict(chain)
            cost = dict(self.chain_cost)

        resting_val = sum(ro.price * ro.size for ro in self.resting.values() if ro is not None)
        committed = cost["YES"] + cost["NO"] + resting_val

        plan = plan_requote(
            yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
            inv_yes=inv["YES"], inv_no=inv["NO"],
            yes_cost=cost["YES"], no_cost=cost["NO"],
            committed=committed, target_shares=self.target_shares,
            resting=self.resting, cfg=self.cfg)

        for cid in plan.cancels:
            for side, ro in self.resting.items():
                if ro is not None and ro.order_id == cid:
                    self.resting[side] = None

        post_cost = sum(q.price * q.size for q in plan.posts)
        if plan.posts and committed + post_cost <= self.cfg.per_market_cap_usd + 1e-9:
            for q in plan.posts:
                self._oid += 1
                self.resting[q.side] = RestingOrder(f"o{self._oid}", q.side, q.price, q.size)
                self.placed_tick[q.side] = t
                self.last_px[q.side] = q.price

        # taker hits our resting bid on that side → we BUY it (ground truth)
        if taker in ("YES", "NO"):
            ro = self.resting[taker]
            if ro is not None and ro.order_id not in self.filled_ids:
                self.real_inv[taker] += ro.size
                self.real_cost[taker] += ro.price * ro.size
                self.filled_ids.add(ro.order_id)

        self.chain_hist.append(dict(self.real_inv))
        self.max_real_naked = max(self.max_real_naked, abs(self.real_inv["YES"] - self.real_inv["NO"]))
        for s in ("YES", "NO"):
            self.max_real_inv[s] = max(self.max_real_inv[s], self.real_inv[s])
        self._t += 1

    def run(self, script):
        for t in script:
            self.tick(*t)


# ── LocalInventory unit invariants ──

def test_credit_fill_adds_shares_and_cost():
    li = LocalInventory()
    li.credit_fill("YES", 5, 0.25)
    assert li.inv["YES"] == 5 and abs(li.cost["YES"] - 1.25) < 1e-9
    assert li.avg("YES") == 0.25


def test_reconcile_up_only_raises_never_lowers():
    li = LocalInventory()
    li.credit_fill("YES", 5, 0.25)
    li.reconcile_up("YES", 3, 0.25)      # chain lower → ignore (lag)
    assert li.inv["YES"] == 5
    li.reconcile_up("YES", 8, 0.30)      # chain higher → raise (partial/external)
    assert li.inv["YES"] == 8


# ── the bug: read-lag over-buys without the fix ──

def test_lag_reproduces_overbuy_without_fix():
    # cheap YES hammered every tick (pair 0.25+0.55=0.80 < $1 so it posts);
    # chain read lags 3 ticks → old loop keeps re-posting the already-filled side.
    sim = LaggedRequoteSim(cfg=cfg(), target_shares=5, read_lag=3, mode="chain")
    sim.run([(0.25, 0.55, "YES")] * 8)
    assert sim.max_real_inv["YES"] >= 15      # over-bought far past target 5
    assert sim.max_real_naked >= 9            # naked cap (5) blown


# ── the fix: LocalInventory caps the over-buy ──

def test_lag_fix_caps_overbuy():
    sim = LaggedRequoteSim(cfg=cfg(), target_shares=5, read_lag=3, mode="local")
    sim.run([(0.25, 0.55, "YES")] * 8)
    # at most one extra in-flight order beyond target — never the 15-share runaway
    assert sim.max_real_inv["YES"] <= 5 + cfg().flat_size
    assert sim.max_real_naked <= 5 + cfg().flat_size


def test_lag_fix_balanced_flow_still_catches_pair():
    sim = LaggedRequoteSim(cfg=cfg(), target_shares=5, read_lag=3, mode="local")
    sim.run([(0.49, 0.49, "YES" if i % 2 == 0 else "NO") for i in range(16)])
    assert min(sim.real_inv["YES"], sim.real_inv["NO"]) >= 1   # still forms pairs
    assert sim.max_real_inv["YES"] <= 5 + cfg().flat_size      # without over-buying
    assert sim.max_real_inv["NO"] <= 5 + cfg().flat_size

# Laddered Re-Quoter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rest a deep ladder of bids on both sides (anchor toggle: static `entry` vs chasing `book`) to capture the cheap-pair edge, with a hard naked-cap that pulls the heavier side's rungs.

**Architecture:** A new pure planner `plan_ladder` (in `quoter/runner/ladder_planner.py`) computes the full multi-rung plan each tick, reusing the cost-basis gate idea; a new live method `_ladder_window` in `merge_runner.py` manages a LIST of resting rungs per side using the existing `LocalInventory`; config knobs gate it on.

**Tech Stack:** Python 3.13, pytest. Reuses `quoter/runner/local_inventory.py`, `quoter/strategy/ladder.py` (`Quote`, `Side`), `quoter/runner/requote_planner.py` (`RestingOrder`).

**Spec:** `docs/superpowers/specs/2026-06-11-ladder-requoter-design.md`

---

## File structure

- `quoter/config.py` — add 6 ladder knobs to the `Config` dataclass.
- `quoter/runner/ladder_planner.py` — NEW pure planner: `LadderPlan`, `_rung_prices`, `plan_ladder`.
- `tests/test_ladder_planner.py` — NEW unit tests for the planner.
- `tests/test_ladder_sim.py` — NEW lag/fill simulator + invariant tests.
- `quoter/runner/merge_runner.py` — add `_ladder_window`; branch `run_forever` to it when `cfg.rungs > 1`.
- `quoter/runner/run_control.py` — set the small first-live ladder config.

---

### Task 1: Config knobs

**Files:**
- Modify: `quoter/config.py` (add fields after line 38, the `min_time_to_expiry_sec` line in the phase-19 block)
- Test: `tests/test_ladder_config.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ladder_config.py`:

```python
from quoter.config import Config


def test_config_has_ladder_knobs_with_defaults():
    c = Config()
    assert c.ladder_anchor == "entry"
    assert c.rungs == 5
    assert c.rung_size == 5
    assert abs(c.rung_spacing - 0.03) < 1e-9
    assert c.naked_cap == 10
    assert abs(c.per_window_cap - 12.0) < 1e-9


def test_config_ladder_knobs_overridable():
    c = Config(ladder_anchor="book", rungs=8, rung_size=6, naked_cap=20)
    assert c.ladder_anchor == "book" and c.rungs == 8 and c.naked_cap == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ladder_config.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'ladder_anchor'`

- [ ] **Step 3: Add the fields**

In `quoter/config.py`, immediately after the line `min_time_to_expiry_sec: float = 5.0  # below this → no quotes` (line 38), add:

```python

    # phase-22 laddered re-quoter
    ladder_anchor: str = "entry"  # "entry" (static from entry-mid) | "book" (chase best bid)
    rungs: int = 5                # rungs per side
    rung_size: int = 5            # shares per rung (Polymarket min 5)
    rung_spacing: float = 0.03    # price step between rungs
    naked_cap: int = 10           # max |inv_yes - inv_no| → pull heavier side's rungs
    per_window_cap: float = 12.0  # $ ceiling on committed spend per window
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ladder_config.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_ladder_config.py
git commit -m "feat(config): ladder re-quoter knobs (anchor/rungs/size/spacing/naked_cap/window_cap)"
```

---

### Task 2: Pure `plan_ladder`

**Files:**
- Create: `quoter/runner/ladder_planner.py`
- Test: `tests/test_ladder_planner.py`

Use `merge_edge=0.02` in tests so δ = `merge_edge/2` = 0.01 gives clean rung prices (top = anchor − 0.01).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ladder_planner.py`:

```python
"""Pure laddered planner: rung placement, anchor toggle, clamps, caps, cost-basis gate."""

from quoter.config import Config
from quoter.runner.ladder_planner import LadderPlan, plan_ladder
from quoter.runner.requote_planner import RestingOrder


def cfg(**kw):
    base = dict(merge_edge=0.02, ladder_anchor="entry", rungs=5, rung_size=5,
                rung_spacing=0.03, naked_cap=10, per_window_cap=12.0)
    base.update(kw)
    return Config(**base)


def _plan(**kw):
    base = dict(yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
                inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
                resting={"YES": [], "NO": []}, c=None)
    base.update(kw)
    c = base.pop("c") or cfg()
    return plan_ladder(
        yes_bid=base["yes_bid"], no_bid=base["no_bid"], yes_ask=base["yes_ask"],
        no_ask=base["no_ask"], entry_mid=base["entry_mid"], inv_yes=base["inv_yes"],
        inv_no=base["inv_no"], yes_cost=base["yes_cost"], no_cost=base["no_cost"],
        committed=base["committed"], resting=base["resting"], cfg=c)


def _prices(posts, side):
    return sorted((q.price for q in posts if q.side == side), reverse=True)


def test_lays_ladder_from_entry_anchor():
    # entry_mid 0.45, δ=0.01 → YES top 0.44, step 0.03 → 0.44,0.41,0.38,0.35,0.32
    p = _plan()
    assert _prices(p.posts, "YES") == [0.44, 0.41, 0.38, 0.35, 0.32]
    # NO anchor = 1-0.45 = 0.55 → top 0.54 → 0.54,0.51,0.48,0.45,0.42
    assert _prices(p.posts, "NO") == [0.54, 0.51, 0.48, 0.45, 0.42]


def test_book_anchor_chases_current_bid():
    # book mode anchors to yes_bid/no_bid, not entry_mid
    p = _plan(yes_bid=0.50, no_bid=0.48, c=cfg(ladder_anchor="book"))
    assert _prices(p.posts, "YES")[0] == 0.49   # 0.50 - δ(0.01)
    assert _prices(p.posts, "NO")[0] == 0.47


def test_ask_clamp_drops_crossing_rungs():
    # yes_ask 0.30 → YES top clamped to 0.29
    p = _plan(yes_ask=0.30)
    assert _prices(p.posts, "YES")[0] == 0.29


def test_pair_ok_blocks_expensive_completion_of_held_leg():
    # hold 5 NO @ 0.62; completing YES must keep pair < $1 → only YES rungs <= 0.37 allowed
    p = _plan(inv_no=5, no_cost=5 * 0.62, committed=5 * 0.62)
    yp = _prices(p.posts, "YES")
    assert all(price <= 0.37 for price in yp)        # 0.38+0.62=1.00 blocked
    assert 0.35 in yp and 0.32 in yp                  # cheap completions allowed


def test_naked_cap_pulls_heavier_side():
    # inv_yes 10, naked = 10 = cap → all YES rungs cancelled, NO rungs still posted
    resting = {"YES": [RestingOrder("y1", "YES", 0.44, 5)], "NO": []}
    p = _plan(inv_yes=10, inv_no=0, yes_cost=10 * 0.30, committed=3.0, resting=resting)
    assert "y1" in p.cancels
    assert not any(q.side == "YES" for q in p.posts)
    assert any(q.side == "NO" for q in p.posts)


def test_capital_cap_pulls_everything():
    resting = {"YES": [RestingOrder("y1", "YES", 0.44, 5)],
               "NO": [RestingOrder("n1", "NO", 0.54, 5)]}
    p = _plan(committed=12.0, resting=resting)
    assert set(p.cancels) == {"y1", "n1"} and p.posts == []


def test_keeps_existing_rung_reposts_missing():
    # one YES rung already resting at 0.44 → kept (not cancelled), the other 4 posted
    resting = {"YES": [RestingOrder("y1", "YES", 0.44, 5)], "NO": []}
    p = _plan(resting=resting)
    assert "y1" not in p.cancels
    assert _prices(p.posts, "YES") == [0.41, 0.38, 0.35, 0.32]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ladder_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'quoter.runner.ladder_planner'`

- [ ] **Step 3: Implement the planner**

Create `quoter/runner/ladder_planner.py`:

```python
"""Pure laddered re-quoting brain — lays a deep ladder of bids both sides. No I/O.

Catches cheap dips (the merge edge the competitor analysis proved real) by resting
rungs below the mid. Anchor is a config toggle: "entry" keeps rungs static at the
window-entry mid (a dip into a low rung fills cheap); "book" chases the current best
bid. A hard naked-cap pulls the heavier side's rungs so we never accumulate a large
directional position (the competitor's losing part). The per-rung cost-basis gate
reuses the over-buy fix: no rung may complete a held leg into a >= $1 pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.strategy.ladder import Quote, Side
from quoter.runner.requote_planner import RestingOrder


@dataclass
class LadderPlan:
    cancels: list[str] = field(default_factory=list)
    posts: list[Quote] = field(default_factory=list)


def _rung_prices(anchor: float, ask: float | None, delta: float,
                 rungs: int, spacing: float) -> list[float]:
    """Descending rung prices from (anchor - delta), clamped 0.01 .. ask-0.01."""
    top = round(anchor - delta, 2)
    if ask and ask > 0:
        top = min(top, round(ask - 0.01, 2))
    out: list[float] = []
    for i in range(rungs):
        p = round(top - i * spacing, 2)
        if p >= 0.01:
            out.append(p)
    return out


def plan_ladder(
    *,
    yes_bid: float,
    no_bid: float,
    yes_ask: float | None,
    no_ask: float | None,
    entry_mid: float,
    inv_yes: int,
    inv_no: int,
    yes_cost: float,
    no_cost: float,
    committed: float,
    resting: dict[str, list[RestingOrder]],
    cfg: Config,
) -> LadderPlan:
    """Return the (cancels, posts) ladder plan for this tick. Pure + deterministic."""
    plan = LadderPlan()

    # Gate — CAPITAL: committed hit the per-window budget → pull everything.
    if committed >= cfg.per_window_cap:
        for side in ("YES", "NO"):
            for ro in resting.get(side, []):
                plan.cancels.append(ro.order_id)
        return plan

    delta = cfg.merge_edge / 2.0
    if cfg.ladder_anchor == "book":
        anchor_yes, anchor_no = yes_bid, no_bid
    else:
        anchor_yes, anchor_no = entry_mid, (1.0 - entry_mid)

    yes_rungs = _rung_prices(anchor_yes, yes_ask, delta, cfg.rungs, cfg.rung_spacing)
    no_rungs = _rung_prices(anchor_no, no_ask, delta, cfg.rungs, cfg.rung_spacing)

    yes_avg = (yes_cost / inv_yes) if inv_yes > 0 else None
    no_avg = (no_cost / inv_no) if inv_no > 0 else None
    naked = inv_yes - inv_no
    target = cfg.rungs * cfg.rung_size

    def pair_ok(side: Side, price: float) -> bool:
        """The pair this rung would form must cost < $1, using the price already PAID
        on a held opposite leg (else the opposite side's top rung as a conservative
        completion estimate)."""
        if side == "YES":
            other = no_avg if (naked < 0 and no_avg is not None) else (no_rungs[0] if no_rungs else 1.0)
            return (price + other) < 1.0
        other = yes_avg if (naked > 0 and yes_avg is not None) else (yes_rungs[0] if yes_rungs else 1.0)
        return (price + other) < 1.0

    desired: dict[str, list[float]] = {"YES": [], "NO": []}
    if inv_yes < target and naked < cfg.naked_cap:
        desired["YES"] = [p for p in yes_rungs if pair_ok("YES", p)]
    if inv_no < target and -naked < cfg.naked_cap:
        desired["NO"] = [p for p in no_rungs if pair_ok("NO", p)]

    for side in ("YES", "NO"):
        want = set(desired[side])
        have_prices: set[float] = set()
        for ro in resting.get(side, []):
            if ro.price in want:
                have_prices.add(ro.price)   # keep
            else:
                plan.cancels.append(ro.order_id)
        for p in desired[side]:
            if p not in have_prices:
                plan.posts.append(Quote(side, p, cfg.rung_size))
    return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ladder_planner.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/ladder_planner.py tests/test_ladder_planner.py
git commit -m "feat(ladder): pure plan_ladder — multi-rung, anchor toggle, naked-cap, cost-basis gate"
```

---

### Task 3: Ladder fill-simulator + invariants

**Files:**
- Create: `tests/test_ladder_sim.py`

Proves end-to-end behavior offline: on a one-sided dump the naked stays bounded by the cap,
and cheap dips fill (the captured pair is < $1). Reuses `LocalInventory`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ladder_sim.py`:

```python
"""Offline ladder simulator: drives plan_ladder with scripted taker fills, applies them
to LocalInventory, and checks the invariants (naked bounded, cheap pairs form)."""

from dataclasses import dataclass, field

from quoter.config import Config
from quoter.runner.ladder_planner import plan_ladder
from quoter.runner.local_inventory import LocalInventory
from quoter.runner.requote_planner import RestingOrder


def cfg(**kw):
    base = dict(merge_edge=0.02, ladder_anchor="entry", rungs=5, rung_size=5,
                rung_spacing=0.03, naked_cap=10, per_window_cap=12.0)
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
    # Down dumps: its rungs (0.54,0.51,0.48,0.45,0.42) fill one by one, no Up fills.
    c = cfg()
    sim = LadderSim(cfg=c, entry_mid=0.45)
    sim.tick(0.44, 0.54)                                  # lay ladders
    for p in [0.54, 0.51, 0.48, 0.45, 0.42]:
        sim.tick(0.44, p, fills=[("NO", p)])
    # naked never exceeds the cap plus at most one in-flight rung
    assert sim.max_naked <= c.naked_cap + c.rung_size
    assert sim.local.inv["NO"] <= c.naked_cap + c.rung_size


def test_cheap_pair_forms_on_two_sided_dips():
    # both sides dip into low rungs → matched pair cost should be < $1
    c = cfg()
    sim = LadderSim(cfg=c, entry_mid=0.45)
    sim.tick(0.44, 0.54)
    sim.tick(0.44, 0.54, fills=[("YES", 0.35), ("NO", 0.45)])   # cheap fills both sides
    iy, ino = sim.local.inv["YES"], sim.local.inv["NO"]
    assert iy > 0 and ino > 0
    pair_cost = sim.local.cost["YES"] / iy + sim.local.cost["NO"] / ino
    assert pair_cost < 1.0                                       # 0.35 + 0.45 = 0.80
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ladder_sim.py -v`
Expected: FAIL with `ImportError` (no `plan_ladder`) — only if Task 2 not done; otherwise the
sim asserts run. If Task 2 is complete, Step 2 instead confirms both tests PASS directly; in
that case note it and proceed to Step 4.

- [ ] **Step 3: (Implementation already exists)**

No new production code — this task only adds the simulator + invariant tests on top of
`plan_ladder` (Task 2) and `LocalInventory`. If a test fails, the bug is in `plan_ladder`;
fix it there and re-run.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ladder_sim.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_ladder_sim.py
git commit -m "test(ladder): offline fill-sim — naked bounded on dump, cheap pair forms"
```

---

### Task 4: Live `_ladder_window` + wiring

**Files:**
- Modify: `quoter/runner/merge_runner.py` (add `_ladder_window`; branch in `run_forever`)
- Modify: `quoter/runner/run_control.py` (small first-live ladder config)

This is the live I/O path (no unit test; validated operator-gated later). It mirrors the
existing `_requote_window` (same constants, grace-reconcile, forward-cap, cancel_all-in-finally,
`LocalInventory`) but manages a LIST of rungs per side and calls `plan_ladder`.

- [ ] **Step 1: Add the import**

In `quoter/runner/merge_runner.py`, after the line
`from quoter.runner.local_inventory import LocalInventory`, add:

```python
from quoter.runner.ladder_planner import plan_ladder
```

- [ ] **Step 2: Branch `run_forever` to the ladder path**

In `quoter/runner/merge_runner.py`, find the branch (around line 204):

```python
                        if self.requote:
                            await self._requote_window(m, mid)
                        else:
                            await self.trade_window(m, mid)
```

Replace it with:

```python
                        if self.requote and self.cfg.rungs > 1:
                            await self._ladder_window(m, mid)
                        elif self.requote:
                            await self._requote_window(m, mid)
                        else:
                            await self.trade_window(m, mid)
```

- [ ] **Step 3: Add the `_ladder_window` method**

In `quoter/runner/merge_runner.py`, add this method immediately after the existing
`_requote_window` method (after its final `log.info("runner_requote_done", ...)` line):

```python
    async def _ladder_window(self, m, mid_at_entry: float) -> None:
        """LIVE laddered re-quoting: rest a deep ladder of bids both sides (anchor per
        cfg.ladder_anchor), credit fills to LocalInventory, cap naked by pulling the
        heavier side's rungs. Operator-gated; the brain (plan_ladder) is sim-tested."""
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        self.state.last_event = f"laddering {m.slug} (mid {mid_at_entry:.2f})"
        log.info("runner_ladder_enter", slug=m.slug, mid=round(mid_at_entry, 3))

        coll_start = self.collateral_usd()
        entry_mid = mid_at_entry
        resting: dict[str, list[RestingOrder]] = {"YES": [], "NO": []}
        placed_at: dict[str, float] = {}     # order_id -> monotonic time placed
        local = LocalInventory()

        try:
            while m.time_remaining() > END_BUFFER_SEC:
                if self.state.force_stop_requested:
                    log.info("runner_ladder_force_break", slug=m.slug)
                    break
                try:
                    async with httpx.AsyncClient(timeout=6) as cl:
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
                except Exception:
                    await asyncio.sleep(REQUOTE_SEC)
                    continue
                yes_bid, no_bid = _best(by, "bids"), _best(bn, "bids")
                yes_ask, no_ask = _best(by, "asks"), _best(bn, "asks")
                if not yes_bid or not no_bid:
                    await asyncio.sleep(REQUOTE_SEC)
                    continue

                chain_yes, chain_no = self._shares(m.yes_token), self._shares(m.no_token)
                coll_now = self.collateral_usd()
                now = monotonic()

                # Credit filled rungs (vanished uncancelled), drop them from the list.
                open_ids = self._open_order_ids()
                for side in ("YES", "NO"):
                    kept = []
                    for ro in resting[side]:
                        if ro.order_id not in open_ids and (now - placed_at.get(ro.order_id, 0.0)) > REQUOTE_SEC:
                            local.credit_fill(side, ro.size, ro.price)
                        else:
                            kept.append(ro)
                    resting[side] = kept
                local.reconcile_up("YES", chain_yes, yes_bid)
                local.reconcile_up("NO", chain_no, no_bid)
                inv_yes, inv_no = local.inv["YES"], local.inv["NO"]

                spent_bal = (coll_start - coll_now) if (coll_start >= 0 and coll_now >= 0) else 0.0
                realized = max(spent_bal, local.cost["YES"] + local.cost["NO"])
                resting_val = sum(ro.price * ro.size for s in ("YES", "NO") for ro in resting[s])
                committed = max(0.0, realized) + resting_val

                plan = plan_ladder(
                    yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
                    entry_mid=entry_mid, inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=local.cost["YES"], no_cost=local.cost["NO"],
                    committed=committed, resting=resting, cfg=self.cfg)

                if plan.cancels:
                    await self.clob.cancel_orders(plan.cancels)
                    for side in ("YES", "NO"):
                        resting[side] = [ro for ro in resting[side] if ro.order_id not in plan.cancels]

                for q in plan.posts:
                    post_cost = q.price * q.size
                    if committed + post_cost > self.cfg.per_window_cap + 1e-9:
                        continue
                    tok = m.yes_token if q.side == "YES" else m.no_token
                    r = await self.clob.place_limit(token_id=tok, price=q.price, size=q.size,
                                                    side="BUY", post_only=True)
                    if r and r.get("order_id"):
                        resting[q.side].append(RestingOrder(r["order_id"], q.side, q.price, q.size))
                        placed_at[r["order_id"]] = now
                        committed += post_cost

                self.state.pairs_caught = min(inv_yes, inv_no)
                self.state.naked_shares = abs(inv_yes - inv_no)
                await asyncio.sleep(REQUOTE_SEC)
        finally:
            await self.cancel_all()

        iy = max(self._shares(m.yes_token), local.inv["YES"])
        inn = max(self._shares(m.no_token), local.inv["NO"])
        self.state.last_event = f"ladder done: matched={min(iy, inn)} naked={abs(iy - inn)}"
        log.info("runner_ladder_done", slug=m.slug, matched=min(iy, inn), naked=abs(iy - inn))
```

- [ ] **Step 4: Verify the whole suite still passes and the module imports**

Run: `.venv/bin/python -c "import quoter.runner.merge_runner" && .venv/bin/python -m pytest -q`
Expected: `merge_runner` imports with no error; full suite all green (no regressions).

- [ ] **Step 5: Set the small first-live ladder config**

In `quoter/runner/run_control.py`, find the `CFG = Config(...)` block (the phase-21 knobs:
`merge_edge=0.01, max_naked_shares=5, merge_levels=1, flat_size=5, per_market_cap_usd=6.0,
min_time_to_expiry_sec=5.0`) and replace that `Config(...)` call with:

```python
CFG = Config(
    merge_edge=0.02, max_naked_shares=5, merge_levels=1,
    flat_size=5, per_market_cap_usd=6.0, min_time_to_expiry_sec=5.0,
    # phase-22 ladder (small first-live). per_window_cap=25 fits the full 5x5 ladder
    # notional (~$21.50); the REAL risk is bounded by naked_cap=10 (~$4.50 unhedged).
    ladder_anchor="entry", rungs=5, rung_size=5, rung_spacing=0.03,
    naked_cap=10, per_window_cap=25.0,
)
```

(`REQUOTE = True` stays as-is; `cfg.rungs > 1` now routes `run_forever` to `_ladder_window`.)

- [ ] **Step 6: Final import + suite check, then commit**

Run: `.venv/bin/python -c "import quoter.runner.run_control" && .venv/bin/python -m pytest -q`
Expected: imports clean; full suite green.

```bash
git add quoter/runner/merge_runner.py quoter/runner/run_control.py
git commit -m "feat(ladder): live _ladder_window + wire run_forever + small first-live config"
```

---

## Self-review

**Spec coverage:**
- Ladder-anchor toggle (entry/book) → Task 1 (config) + Task 2 (`plan_ladder` anchor branch + `test_book_anchor_chases_current_bid`) ✓
- Static rung prices / spacing / clamp → Task 2 `_rung_prices` + `test_lays_ladder_from_entry_anchor`, `test_ask_clamp_drops_crossing_rungs` ✓
- Capital cap → Task 2 gate + `test_capital_cap_pulls_everything` ✓
- Naked-cap pulls heavier side → Task 2 + `test_naked_cap_pulls_heavier_side`; invariant in Task 3 `test_naked_bounded_on_one_sided_dump` ✓
- pair_ok per rung (cost-basis) → Task 2 + `test_pair_ok_blocks_expensive_completion_of_held_leg` ✓
- Per-side target + re-post filled rungs → Task 2 `target` guard + `test_keeps_existing_rung_reposts_missing` ✓
- Live loop: list-of-rungs, LocalInventory credit/reconcile, forward-cap, cancel_all-in-finally, entry_mid anchor → Task 4 `_ladder_window` ✓
- Branch on `cfg.rungs > 1` → Task 4 Step 2 ✓
- Small first-live config → Task 4 Step 5 ✓
- Cheap pair forms (edge exists) → Task 3 `test_cheap_pair_forms_on_two_sided_dips` ✓

**Placeholder scan:** none — every step has full code/commands.

**Type consistency:** `Quote(side, price, size)`, `RestingOrder(order_id, side, price, size)`,
`LadderPlan(cancels, posts)`, `plan_ladder(...)` keyword signature, and `LocalInventory`
(`inv`/`cost` dicts, `credit_fill`, `reconcile_up`) are used identically across Tasks 2, 3, 4.
`cfg` fields (`ladder_anchor`, `rungs`, `rung_size`, `rung_spacing`, `naked_cap`,
`per_window_cap`, `merge_edge`) match Task 1. The live loop reuses existing module-level
helpers `_best`, `END_BUFFER_SEC`, `REQUOTE_SEC`, `monotonic`, `httpx`, `asyncio` already
imported in `merge_runner.py`.

# Sweep-Proof Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Bound naked under a fast dump (immediate optimistic credit) WITHOUT re-opening the phantom-fill bug (real-fills `reconcile_down` after a grace), proven by a lag-modeling simulation that first reproduces the sweep.

**Architecture:** The ladder loop's posting/cap inventory goes back to the optimistic `LocalInventory` (credit-on-vanish = immediate = sweep-proof). A new `reconcile_down` lowers it to the real-fills count when an optimistic credit is never confirmed (phantom). A `LagSim` models feed delay + vanish + dump + phantom to prove both behaviors.

**Tech Stack:** Python 3.13, pytest. Reuses `fetch_window_fills`, `inventory_from_fills`, `plan_naked_action`, `plan_ladder`.

**Spec:** `docs/superpowers/specs/2026-06-13-sweep-proof-inventory-design.md`

**Operational:** Work on `master`, local + unit tests ONLY. NO live run until the 3 lag-sim tests + full suite are green AND a review passes. Server <SERVER_IP>, bot STOPPED, cash ~$73.22.

---

### Task 1: `LocalInventory.reconcile_down` (phantom kill)

**Files:**
- Modify: `quoter/runner/local_inventory.py`
- Test: `tests/test_local_inventory.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_local_inventory.py
def test_reconcile_down_lowers_after_grace():
    inv = LocalInventory()
    inv.credit_fill("NO", 10, 0.40)          # optimistic 10
    inv.reconcile_down("NO", 5, now=0.0, grace=6.0)   # real=5, gap opens
    assert inv.inv["NO"] == 10                          # not yet (grace not elapsed)
    inv.reconcile_down("NO", 5, now=3.0, grace=6.0)
    assert inv.inv["NO"] == 10                          # still within grace
    inv.reconcile_down("NO", 5, now=6.0, grace=6.0)
    assert inv.inv["NO"] == 5                            # persisted >= grace -> trust real
    assert abs(inv.cost["NO"] - 5 * 0.40) < 1e-9        # cost scaled to remaining


def test_reconcile_down_real_fill_not_reversed():
    inv = LocalInventory()
    inv.credit_fill("NO", 5, 0.40)           # optimistic 5 (a real fill, feed lags)
    inv.reconcile_down("NO", 0, now=0.0, grace=6.0)    # real still 0 (lag) -> timer starts
    inv.reconcile_down("NO", 5, now=3.0, grace=6.0)    # real catches up < grace
    assert inv.inv["NO"] == 5                            # NOT reversed (gap cleared in time)


def test_reconcile_down_noop_when_real_ge_local():
    inv = LocalInventory()
    inv.credit_fill("YES", 5, 0.50)
    inv.reconcile_down("YES", 9, now=10.0, grace=6.0)  # real higher -> reconcile_down ignores
    assert inv.inv["YES"] == 5
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_local_inventory.py -q`
Expected: FAIL with `AttributeError: 'LocalInventory' object has no attribute 'reconcile_down'`

- [ ] **Step 3: Implement**

In `quoter/runner/local_inventory.py`, add an `_over_since` field to the dataclass (alongside `inv`/`cost`):

```python
    _over_since: dict = field(default_factory=lambda: {"YES": None, "NO": None})
```

Add this method after `reconcile_up`:

```python
    def reconcile_down(self, side: Side, real_qty: int, now: float, grace: float) -> None:
        """Phantom kill. If the optimistic local count exceeds the REAL-fills count
        for longer than ``grace`` seconds, the excess credit was never confirmed by a
        real fill (it was a phantom — a vanished-but-unfilled order) → lower local to
        real. A genuine fill appears in the real feed within the feed lag (< grace),
        clearing the gap before this triggers, so real fills are never reversed.
        ``grace`` MUST exceed the real-fills feed lag."""
        if self.inv[side] <= real_qty:
            self._over_since[side] = None
            return
        if self._over_since[side] is None:
            self._over_since[side] = now
        elif now - self._over_since[side] >= grace:
            avg = self.cost[side] / self.inv[side] if self.inv[side] > 0 else 0.0
            self.inv[side] = real_qty
            self.cost[side] = real_qty * avg
            self._over_since[side] = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_local_inventory.py -q`
Expected: PASS (all, including the 3 new).

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/local_inventory.py tests/test_local_inventory.py
git commit -m "feat(inventory): reconcile_down — phantom kill after a grace > feed lag"
```

---

### Task 2: `LagSim` — reproduce the sweep, prove the fix (CENTERPIECE)

**Files:**
- Create: `tests/test_sweep_sim.py`

This sim models the REAL defect: a delayed real-fills feed, immediate vanish-detection, a fast
dump, and an injectable phantom. Two inventory modes: `"lagged"` (posting from delayed
real-fills = the current buggy behavior) and `"fixed"` (optimistic `LocalInventory` +
`reconcile_down`). `_true` tracks what we ACTUALLY hold (ground truth for `max_naked`).

- [ ] **Step 1: Write the failing tests (full sim + 3 tests)**

```python
# tests/test_sweep_sim.py
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
```

- [ ] **Step 2: Run to verify the split outcome**

Run: `.venv/bin/python -m pytest tests/test_sweep_sim.py -q`
Expected: all 3 PASS. `test_dump_sweep_reproduced_in_lagged_mode` asserts the BUG exists in
`lagged` mode (sanity that the sim is faithful); the other two assert the fix bounds it and
corrects phantoms. If `test_dump_sweep_reproduced_in_lagged_mode` does NOT show a sweep, the sim
is not faithful — fix the sim (e.g. raise tick count or confirm `nd` re-posting) until lagged
mode genuinely sweeps, because that is the proof the fix matters.

- [ ] **Step 3: Commit**

```bash
git add tests/test_sweep_sim.py
git commit -m "test(sweep): lag-modeling sim reproduces dump-sweep, proves optimistic fix + phantom kill"
```

---

### Task 3: Config knob `inv_reconcile_grace_sec`

**Files:**
- Modify: `quoter/config.py` (after the auto-flat knobs)
- Test: `tests/test_ladder_config.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ladder_config.py
def test_inv_reconcile_grace_default():
    c = Config()
    assert c.inv_reconcile_grace_sec == 12.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ladder_config.py -q`
Expected: FAIL with `AttributeError`/`TypeError` on `inv_reconcile_grace_sec`.

- [ ] **Step 3: Implement**

In `quoter/config.py`, after `flatten_grace_sec`:

```python
    inv_reconcile_grace_sec: float = 12.0   # phantom-kill grace; MUST exceed data-api feed lag
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ladder_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_ladder_config.py
git commit -m "feat(config): inv_reconcile_grace_sec knob (phantom-kill grace)"
```

---

### Task 4: Live wiring — optimistic posting inventory + real-fills reconcile

**Files:**
- Modify: `quoter/runner/merge_runner.py` (`_ladder_window`)

I/O glue — no new unit test (proven by Task 2 sim + suite green). Restore the vanish-detection
(remove filled orders from `resting` + immediate optimistic credit), reconcile with the real
feed, and feed the optimistic `LocalInventory` to `plan_ladder` and `plan_naked_action`.

- [ ] **Step 1: Re-add the optimistic inventory state**

In `_ladder_window`, replace the line `last_inv = inventory_from_fills([])` with:

```python
        local = LocalInventory()              # optimistic posting/cap inventory (sweep-proof)
        last_real = inventory_from_fills([])   # last good real-fills inventory (phantom-kill)
```

(`LocalInventory` is already imported at the top of the module.)

- [ ] **Step 2: Restore vanish-detection + reconcile in the inventory block**

Replace the current real-fills inventory block (the `try: fills = await fetch_window_fills(...)`
... `inv = last_inv; inv_yes, inv_no = inv.inv["YES"], inv.inv["NO"]` block) with:

```python
                    # Credit OUR vanished-uncancelled rungs immediately (optimistic, lag-proof)
                    # and drop them from resting so the ladder stays coherent.
                    open_ids = self._open_order_ids()
                    for side in ("YES", "NO"):
                        kept = []
                        for ro in resting[side]:
                            if ro.order_id not in open_ids and (now - placed_at.get(ro.order_id, 0.0)) > REQUOTE_SEC:
                                local.credit_fill(side, ro.size, ro.price)
                                placed_at.pop(ro.order_id, None)
                            else:
                                kept.append(ro)
                        resting[side] = kept
                    # Reconcile the optimistic count against the REAL fills feed: raise to real
                    # (caught fills), and lower to real after the grace (kills phantom credits).
                    try:
                        fills = await fetch_window_fills(self.creds.funder, m.slug, cl)
                        last_real = inventory_from_fills(fills)
                        inv_ok = True
                    except Exception:
                        inv_ok = False
                    for side in ("YES", "NO"):
                        local.reconcile_up(side, last_real.inv[side], last_px[side] or (yes_bid if side == "YES" else no_bid))
                        local.reconcile_down(side, last_real.inv[side], now, self.cfg.inv_reconcile_grace_sec)
                    inv = local
                    inv_yes, inv_no = inv.inv["YES"], inv.inv["NO"]
```

(Keep `placed_at[r["order_id"]] = now` where orders are posted — it already exists in the post
loop. `_open_order_ids()` already exists on the runner.)

- [ ] **Step 3: Point `realized`/`committed` and the naked gate at the optimistic `inv`**

The `realized = max(spent_bal, inv.cost["YES"] + inv.cost["NO"])` line already reads `inv.cost`
— now `inv` is the optimistic `local`, so it is correct without change. The `plan_naked_action`
call already uses `inv.avg(...)` and `inv_yes/inv_no` — also correct. Verify both reference
`inv` (the new `local`), not a stale name. The `plan_ladder(...)` call already passes
`inv_yes/inv_no` and `inv.cost[...]` — correct.

- [ ] **Step 4: Update the end-of-window summary**

If the summary reads `last_inv.inv[...]`, change it to `local.inv[...]` (the optimistic count).
Grep `_ladder_window` for `last_inv` and replace any remaining reference with `local`.

- [ ] **Step 5: Run the full suite + import check**

Run: `.venv/bin/python -m pytest -q` → expect all green.
Run: `.venv/bin/python -c "import quoter.runner.merge_runner, quoter.runner.run_control; print('import ok')"`.

- [ ] **Step 6: Commit**

```bash
git add quoter/runner/merge_runner.py
git commit -m "fix(ladder): optimistic posting inventory + real-fills reconcile — sweep-proof, phantom-safe"
```

---

## After all tasks

- [ ] Final code review over the diff: confirm (a) optimistic credit bounds posting (sweep
  cannot recur), (b) `reconcile_down` only fires after grace (no real-fill reversal), (c)
  resting is coherent (filled orders removed), (d) `auto_flat=False` path intact, (e) the naked
  action uses the reconciled `inv`.
- [ ] Report to the user: the 3 lag-sim tests + full suite green, review verdict; deploy +
  data-api re-validation + a SHORT live test are the gated next steps. Do NOT launch live
  without the user's explicit "go".
- [ ] Use superpowers:finishing-a-development-branch.

## Self-Review notes (author)

- **Spec coverage:** reconcile_down (Task 1) ✓; lag-sim reproduce+fix+phantom (Task 2) ✓; grace
  knob (Task 3) ✓; live optimistic+reconcile+resting-coherence (Task 4) ✓.
- **The reproduce test (Task 2) is deliberately a "the bug exists" assertion** — it guards
  against an unfaithful sim that would make the fix look good for the wrong reason.
- **Type consistency:** `reconcile_down(side, real_qty, now, grace)`, `_over_since`, `local`
  (LocalInventory), `last_real` (Inventory from fills) used consistently.
- **YAGNI:** no WS fills, no lag measurement, no legacy-path change.

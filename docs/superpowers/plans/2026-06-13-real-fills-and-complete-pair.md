# Real-Fills Inventory + Complete-Pair Flatten — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ladder loop's inventory come from REAL fills (kills the phantom-fill bug), and make a naked leg COMPLETE into a pair before selling (kills the pair-breaking flatten).

**Architecture:** Replace the "order vanished ⇒ assume filled" heuristic with a stateless rebuild of inventory from our actual window trades. Replace `plan_flatten` (always SELL) with `plan_naked_action` (COMPLETE the pair if it would cost < $1, else SELL). Suppress a side only on a real SELL. Pure logic is unit-tested; the live loop is glue proven by an offline sim.

**Tech Stack:** Python 3.13, pytest, httpx (already used in the loop for books), `py-clob-client-v2` (FOK orders), Polymarket data-api `/activity` (fills source — shape verified this session).

**Spec:** `docs/superpowers/specs/2026-06-13-real-fills-and-complete-pair-design.md`

**Deviation from spec (deliberate):** the spec named CLOB `get_trades` as the fills source. We use the data-api `/activity?type=TRADE` endpoint instead: its response shape (`side`, `outcome`, `size`, `price`, `slug`) was verified live this session, whereas `get_trades`'s shape is unverified and the server is currently SSH-unreachable. Both return our own trades. A validation step (Task 5) re-confirms the mapping against one real response before any live deploy.

**Operational:** Work on `master`. Local code + unit tests ONLY — never launch live (operator-gated; AWS ca-central-1; bot currently STOPPED, server SSH-down). Deploy is a later gated step.

**Fill shape (used everywhere):** a normalized fill is
`{"side": "YES"|"NO", "action": "BUY"|"SELL", "size": float, "price": float}`
(outcome "Up" → side "YES", "Down" → "NO").

---

### Task 1: Replace `plan_flatten` with `plan_naked_action`

**Files:**
- Modify: `quoter/runner/flatten_planner.py` (replace contents)
- Rewrite: `tests/test_flatten_planner.py`

- [ ] **Step 1: Rewrite the test file**

Replace the entire contents of `tests/test_flatten_planner.py` with:

```python
from quoter.runner.flatten_planner import plan_naked_action, NakedAction


def test_below_cap_returns_none():
    assert plan_naked_action(8, 5, 0.5, 0.5, 0.5, 0.5, naked_cap=5) is None  # naked 3 < 5


def test_complete_when_other_side_cheap_yes_heavy():
    # naked +5 (YES heavy), held YES avg 0.61, NO ask 0.36 -> pair 0.97 < 1 -> COMPLETE NO
    a = plan_naked_action(10, 5, 0.61, None, 0.99, 0.36, naked_cap=5)
    assert a == NakedAction(kind="COMPLETE", side="NO", qty=5)


def test_complete_when_other_side_cheap_no_heavy():
    # naked -5 (NO heavy), held NO avg 0.40, YES ask 0.30 -> pair 0.70 < 1 -> COMPLETE YES
    a = plan_naked_action(5, 10, None, 0.40, 0.30, 0.99, naked_cap=5)
    assert a == NakedAction(kind="COMPLETE", side="YES", qty=5)


def test_sell_when_pair_would_exceed_one():
    # naked +5, YES avg 0.61, NO ask 0.45 -> pair 1.06 >= 1 -> SELL the heavy YES
    a = plan_naked_action(10, 5, 0.61, None, 0.99, 0.45, naked_cap=5)
    assert a == NakedAction(kind="SELL", side="YES", qty=5)


def test_sell_fallback_when_ask_missing():
    # no NO ask available -> cannot complete -> SELL heavy YES
    a = plan_naked_action(10, 5, 0.61, None, 0.99, None, naked_cap=5)
    assert a == NakedAction(kind="SELL", side="YES", qty=5)


def test_sell_when_heavy_avg_unknown():
    # heavy avg None (shouldn't happen, but be safe) -> cannot price completion -> SELL
    a = plan_naked_action(10, 5, None, None, 0.99, 0.10, naked_cap=5)
    assert a == NakedAction(kind="SELL", side="YES", qty=5)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_flatten_planner.py -q`
Expected: FAIL with `ImportError: cannot import name 'plan_naked_action'`

- [ ] **Step 3: Replace `flatten_planner.py` contents**

```python
"""Pure decision for a naked (one-sided) leg. No timing, no I/O — the live loop
owns the grace clock and calls this only once the grace has elapsed.

Prefer to COMPLETE the pair (buy the missing/light side) over selling, whenever
the completed pair would still cost < $1 — that locks a guaranteed $1 payout for
< $1, strictly better than dumping the naked leg. Only SELL (flatten) the heavy
side when completion is too expensive (pair >= $1) or the light side has no ask.
"""

from __future__ import annotations

from dataclasses import dataclass

Side = str  # "YES" | "NO"


@dataclass(frozen=True)
class NakedAction:
    kind: str   # "COMPLETE" (buy the light side) | "SELL" (sell the heavy side)
    side: Side  # COMPLETE: the light side to BUY; SELL: the heavy side to SELL
    qty: int    # shares == |naked|


def plan_naked_action(
    inv_yes: int, inv_no: int,
    yes_avg: float | None, no_avg: float | None,
    yes_ask: float | None, no_ask: float | None,
    naked_cap: int,
) -> NakedAction | None:
    """Decide what to do with a naked leg. None if |naked| < naked_cap."""
    naked = inv_yes - inv_no
    if abs(naked) < naked_cap:
        return None
    if naked > 0:
        heavy, light = "YES", "NO"
        heavy_avg, light_ask = yes_avg, no_ask
    else:
        heavy, light = "NO", "YES"
        heavy_avg, light_ask = no_avg, yes_ask
    qty = abs(naked)
    if (light_ask is not None and light_ask > 0 and heavy_avg is not None
            and (heavy_avg + light_ask) < 1.0):
        return NakedAction(kind="COMPLETE", side=light, qty=qty)
    return NakedAction(kind="SELL", side=heavy, qty=qty)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_flatten_planner.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/flatten_planner.py tests/test_flatten_planner.py
git commit -m "feat(flatten): plan_naked_action — complete the pair before selling"
```

---

### Task 2: Pure `inventory_from_fills`

**Files:**
- Create: `quoter/runner/fill_inventory.py`
- Test: `tests/test_fill_inventory.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fill_inventory.py
from quoter.runner.fill_inventory import inventory_from_fills


def _f(side, action, size, price):
    return {"side": side, "action": action, "size": size, "price": price}


def test_buys_only_two_sided():
    inv = inventory_from_fills([
        _f("YES", "BUY", 10, 0.46), _f("NO", "BUY", 10, 0.40)])
    assert inv.inv["YES"] == 10 and inv.inv["NO"] == 10
    assert abs(inv.cost["YES"] - 4.60) < 1e-9
    assert abs(inv.avg("YES") - 0.46) < 1e-9
    assert abs(inv.avg("NO") - 0.40) < 1e-9


def test_window2_one_sided_is_naked_not_balanced():
    # the bug case: 10 Up bought (two fills), 0 Down -> naked 10, NOT 0
    inv = inventory_from_fills([
        _f("YES", "BUY", 5, 0.27), _f("YES", "BUY", 5, 0.36)])
    assert inv.inv["YES"] == 10 and inv.inv["NO"] == 0
    assert (inv.inv["YES"] - inv.inv["NO"]) == 10
    assert inv.avg("NO") is None


def test_sell_reduces_net_qty():
    inv = inventory_from_fills([
        _f("YES", "BUY", 10, 0.50), _f("YES", "SELL", 4, 0.40)])
    assert inv.inv["YES"] == 6           # 10 bought - 4 sold
    assert abs(inv.cost["YES"] - 5.00) < 1e-9   # buy cost basis unchanged by the sell
    assert abs(inv.avg("YES") - 0.50) < 1e-9    # avg of BUYS


def test_empty_fills():
    inv = inventory_from_fills([])
    assert inv.inv["YES"] == 0 and inv.inv["NO"] == 0
    assert inv.avg("YES") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fill_inventory.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'quoter.runner.fill_inventory'`

- [ ] **Step 3: Write the implementation**

```python
# quoter/runner/fill_inventory.py
"""Pure: rebuild per-side inventory from the window's REAL fills (ground truth),
replacing the live loop's old "order vanished ⇒ assume filled" heuristic that
produced phantom fills. Stateless — recompute from the full fill list each tick,
so it never drifts and self-corrects as the fills feed settles.

A fill is {"side": "YES"|"NO", "action": "BUY"|"SELL", "size": float, "price": float}.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Side = str  # "YES" | "NO"


@dataclass
class Inventory:
    inv: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})
    cost: dict = field(default_factory=lambda: {"YES": 0.0, "NO": 0.0})
    buy_qty: dict = field(default_factory=lambda: {"YES": 0, "NO": 0})

    def avg(self, side: Side) -> float | None:
        """Average BUY price of the side (cost basis of held shares); None if no buys."""
        return self.cost[side] / self.buy_qty[side] if self.buy_qty[side] > 0 else None


def inventory_from_fills(fills: list[dict]) -> Inventory:
    """Net inventory = BUY size − SELL size per side; cost = BUY cost basis."""
    out = Inventory()
    for f in fills:
        side = f["side"]
        if side not in ("YES", "NO"):
            continue
        size = f["size"]
        if f["action"] == "BUY":
            out.inv[side] += size
            out.cost[side] += size * f["price"]
            out.buy_qty[side] += size
        elif f["action"] == "SELL":
            out.inv[side] -= size
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_fill_inventory.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/fill_inventory.py tests/test_fill_inventory.py
git commit -m "feat(inventory): inventory_from_fills — ground-truth inventory from real fills"
```

---

### Task 3: `fetch_window_fills` (data-api fills feed)

**Files:**
- Create: `quoter/runner/fills_feed.py`
- Test: `tests/test_fills_feed.py`

- [ ] **Step 1: Write the failing test**

`fetch_window_fills` matches on the EXACT market `slug` (not just the open_ts), so trades
from another asset or another window are excluded even if they share the ts. The fake http
client mimics `httpx.AsyncClient.get`.

```python
# tests/test_fills_feed.py
import asyncio
from quoter.runner.fills_feed import fetch_window_fills


class _FakeResp:
    def __init__(self, data): self._data = data
    def json(self): return self._data
    def raise_for_status(self): pass


class _FakeHttp:
    def __init__(self, data): self._data = data
    async def get(self, url, params=None, headers=None):
        return _FakeResp(self._data)


def test_filters_to_window_and_normalizes_outcome():
    data = [
        {"slug": "btc-updown-5m-1781302200", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.27},
        {"slug": "btc-updown-5m-1781302200", "side": "SELL", "outcome": "Down", "size": 5, "price": 0.18},
        {"slug": "btc-updown-5m-1781301900", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.40},
        {"slug": "eth-updown-5m-1781302200", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.40},
    ]
    fills = asyncio.run(fetch_window_fills("0xFUND", "btc-updown-5m-1781302200", _FakeHttp(data)))
    assert len(fills) == 2
    assert fills[0] == {"side": "YES", "action": "BUY", "size": 5.0, "price": 0.27}
    assert fills[1] == {"side": "NO", "action": "SELL", "size": 5.0, "price": 0.18}


def test_empty_when_no_match():
    http = _FakeHttp([{"slug": "btc-updown-5m-1781301900", "side": "BUY", "outcome": "Up", "size": 5, "price": 0.4}])
    assert asyncio.run(fetch_window_fills("0xFUND", "btc-updown-5m-1781302200", http)) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fills_feed.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'quoter.runner.fills_feed'`

- [ ] **Step 3: Write the implementation**

```python
# quoter/runner/fills_feed.py
"""Fetch our REAL fills for one window from the Polymarket data-api activity feed.

Replaces the live loop's "order vanished ⇒ filled" guess. The data-api returns
our trades keyed by funder (proxy) address; we filter to the exact window slug and
normalize outcome→side. Shape verified live 2026-06-13:
  {"slug": str, "side": "BUY"|"SELL", "outcome": "Up"|"Down", "size": num, "price": num}
"""

from __future__ import annotations

ACTIVITY_URL = "https://data-api.polymarket.com/activity"
_UA = {"User-Agent": "Mozilla/5.0"}


async def fetch_window_fills(funder: str, slug: str, http) -> list[dict]:
    """Return normalized fills for `slug`:
    {"side": "YES"|"NO", "action": "BUY"|"SELL", "size": float, "price": float}.
    `http` is an httpx.AsyncClient (or compatible) with `.get`."""
    resp = await http.get(
        ACTIVITY_URL,
        params={"user": funder, "type": "TRADE", "limit": 500},
        headers=_UA,
    )
    resp.raise_for_status()
    out: list[dict] = []
    for t in resp.json():
        if str(t.get("slug", "")) != slug:
            continue
        out.append({
            "side": "YES" if t.get("outcome") == "Up" else "NO",
            "action": t.get("side"),
            "size": float(t.get("size", 0)),
            "price": float(t.get("price", 0)),
        })
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_fills_feed.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/fills_feed.py tests/test_fills_feed.py
git commit -m "feat(fills): fetch_window_fills — real fills from data-api activity"
```

---

### Task 4: Migrate the ladder sim to real-fills inventory + `plan_naked_action`

**Files:**
- Modify: `tests/test_ladder_sim.py` (rebuild `LadderSim` to use fills + new action; replace the old flatten test)

This proves the integrated behavior offline: inventory rebuilt from a fills list via `inventory_from_fills`, naked handled by `plan_naked_action` (COMPLETE buys the light side, SELL sells the heavy side and suppresses it).

- [ ] **Step 1: Replace the imports and `LadderSim` in `tests/test_ladder_sim.py`**

Change the imports at the top from the `LocalInventory`/`plan_flatten` ones to:

```python
from dataclasses import dataclass, field
from quoter.config import Config
from quoter.runner.fill_inventory import inventory_from_fills
from quoter.runner.flatten_planner import plan_naked_action
from quoter.runner.ladder_planner import plan_ladder
from quoter.runner.requote_planner import RestingOrder
```

Replace the entire `LadderSim` dataclass and its `tick` method with this fills-based model:

```python
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
```

- [ ] **Step 2: Replace the existing tests in `tests/test_ladder_sim.py`**

The existing `test_naked_bounded_on_one_sided_dump` and `test_cheap_pair_forms_on_two_sided_dips` reference `sim.local.inv` — update them to read inventory via `sim._inv()`. The old `test_auto_flat_kills_persistent_naked_and_suppresses` is replaced by two scenario tests. Replace all tests below the `LadderSim` class with:

```python
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
    # fill 5 YES @ 0.61 by hand (taker hit our YES bid)
    sim.tick(0.61, 0.39)
    sim.tick(0.61, 0.39, fills=[("YES", 0.61)])   # naked 5 YES appears
    # next ticks: NO ask cheap (0.39) -> 0.61+0.39=1.00? use 0.36 to be < 1
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
    sim.tick(0.40, 0.60, fills=[("NO", 0.60)])    # naked 5 NO @ 0.60
    sim.tick(0.40, 0.60, yes_ask=0.55)            # 0.60+0.55=1.15 >=1 -> SELL NO
    assert sim.flatten_count >= 1
    assert sim.complete_count == 0
    assert "NO" in sim.flattened                   # SELL suppresses
    assert abs(sim._inv().inv["YES"] - sim._inv().inv["NO"]) < c.naked_cap
```

- [ ] **Step 3: Run to verify**

Run: `.venv/bin/python -m pytest tests/test_ladder_sim.py -q`
Expected: PASS (5 passed). If `test_naked_completes_into_pair_when_other_side_cheap` fails on the exact tick the action fires, adjust `grace_ticks`/the number of follow-up ticks so the grace elapses while the cheap NO ask is present — the assertion content stays the same.

- [ ] **Step 4: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. `tests/test_local_inventory.py` still passes (LocalInventory itself is unchanged; only the ladder loop and sim stop using it). If any test imports `plan_flatten` or `FlattenDecision`, it is stale — only `test_flatten_planner.py` referenced them and Task 1 rewrote it.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ladder_sim.py
git commit -m "test(ladder-sim): real-fills inventory + complete-or-sell, prove no phantom/no broken pair"
```

---

### Task 5: Live wiring in `_ladder_window`

**Files:**
- Modify: `quoter/runner/merge_runner.py` (imports near top; `_ladder_window` inventory block + naked gate)

I/O glue — no new unit test (proven by Task 4 sim + suite stays green). Translate the sim's gate exactly.

- [ ] **Step 1: Update imports**

In `quoter/runner/merge_runner.py`, replace the `from quoter.runner.flatten_planner import plan_flatten` line with:

```python
from quoter.runner.flatten_planner import plan_naked_action
from quoter.runner.fill_inventory import inventory_from_fills
from quoter.runner.fills_feed import fetch_window_fills
```

The `from quoter.runner.local_inventory import LocalInventory` import stays (legacy `_requote_window` still uses it); the ladder path just stops calling it.

- [ ] **Step 2: Replace the inventory block in `_ladder_window`**

Find the per-window state init (`local = LocalInventory()` and the `flattened`/`naked_since` lines added by the auto-flat feature). Replace `local = LocalInventory()` with:

```python
        last_inv = inventory_from_fills([])   # last good inventory (real fills)
```

Then find, inside the `while` loop, the block that credits vanished orders and reconciles (the loop over `resting[side]` calling `local.credit_fill`, plus the two `local.reconcile_up(...)` lines and `inv_yes, inv_no = local.inv[...]`). Replace that ENTIRE block with a real-fills read:

```python
                try:
                    fills = await fetch_window_fills(self.creds.funder, m.slug, cl)
                    last_inv = inventory_from_fills(fills)
                    inv_ok = True
                except Exception:
                    inv_ok = False     # reuse last_inv; skip new posts this tick
                inv = last_inv
                inv_yes, inv_no = inv.inv["YES"], inv.inv["NO"]
```

(`cl` is the `httpx.AsyncClient` already open in the loop for the book fetch — reuse it. The resting-order list is still maintained below for cancel/repost diffing, but it NO LONGER credits fills.) Remove the now-dead `placed_at` fill-crediting use; keep `placed_at` only if still needed for post bookkeeping (it is set when posting; leave the assignment, drop the credit-on-vanish read).

- [ ] **Step 3: Replace the naked gate with `plan_naked_action`**

Replace the auto-flat gate block (the `if self.cfg.auto_flat:` block that called `plan_flatten` and did the SELL) with:

```python
                if self.cfg.auto_flat and inv_ok:
                    naked = inv_yes - inv_no
                    heavy = "YES" if naked > 0 else ("NO" if naked < 0 else None)
                    for s in ("YES", "NO"):
                        if s != heavy:
                            naked_since[s] = None
                    if heavy and abs(naked) >= self.cfg.naked_cap and heavy not in flattened:
                        if naked_since[heavy] is None:
                            naked_since[heavy] = now
                        grace_done = (now - naked_since[heavy]) >= self.cfg.flatten_grace_sec
                        near_end = m.time_remaining() <= self.cfg.flatten_grace_sec
                        if grace_done or near_end:
                            a = plan_naked_action(inv_yes, inv_no, inv.avg("YES"),
                                                  inv.avg("NO"), yes_ask, no_ask,
                                                  self.cfg.naked_cap)
                            if a and a.kind == "COMPLETE":
                                tok = m.yes_token if a.side == "YES" else m.no_token
                                px = yes_ask if a.side == "YES" else no_ask
                                if px:
                                    r = await self.clob.place_limit(
                                        token_id=tok, price=px, size=a.qty,
                                        side="BUY", post_only=False, order_type="FOK")
                                    if r and r.get("order_id"):
                                        log.info("runner_ladder_complete", slug=m.slug,
                                                 side=a.side, qty=a.qty, price=round(px, 3))
                                        naked_since[heavy] = None
                            elif a and a.kind == "SELL":
                                tok = m.yes_token if a.side == "YES" else m.no_token
                                bid = yes_bid if a.side == "YES" else no_bid
                                r = await self.clob.place_limit(
                                    token_id=tok, price=bid, size=a.qty,
                                    side="SELL", post_only=False, order_type="FOK")
                                if r and r.get("order_id"):
                                    flattened.add(a.side)
                                    log.info("runner_ladder_flatten", slug=m.slug,
                                             side=a.side, qty=a.qty, price=round(bid, 3))
                    elif heavy and abs(naked) < self.cfg.naked_cap:
                        naked_since[heavy] = None
```

Note: after a COMPLETE or SELL, inventory updates on the NEXT tick's `fetch_window_fills` (the FOK fill appears in the activity feed) — we do not locally mutate inventory, matching the real-fills design. The `plan_ladder(...)` call keeps `suppressed=frozenset(flattened)` unchanged.

- [ ] **Step 4: Guard posting when inventory is unknown**

In the posting loop (`for q in plan.posts:`), add an early skip when the fills read failed this tick, so we never post on stale inventory. At the top of the posts loop body, before placing, ensure the cancels still run but posts are gated:

```python
                if plan.cancels:
                    await self.clob.cancel_orders(plan.cancels)
                    for side in ("YES", "NO"):
                        resting[side] = [ro for ro in resting[side] if ro.order_id not in plan.cancels]

                for q in plan.posts:
                    if not inv_ok:
                        break               # inventory unknown this tick -> don't post
                    post_cost = q.price * q.size
                    ...                      # rest of the existing posting body unchanged
```

- [ ] **Step 5: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all green). Also verify imports: `.venv/bin/python -c "import quoter.runner.merge_runner, quoter.runner.run_control; print('import ok')"`.

- [ ] **Step 6: Pre-deploy validation note (do NOT deploy here)**

Add nothing to code. Record in the commit body that, before the next live run, one real `fetch_window_fills` response must be eyeballed on the server to confirm the data-api `slug`/`side`/`outcome`/`size`/`price` fields still match (the bot was diagnosed against this exact shape on 2026-06-13). This is a human/operator gate, not code.

- [ ] **Step 7: Commit**

```bash
git add quoter/runner/merge_runner.py
git commit -m "feat(ladder): real-fills inventory + complete-or-sell naked handling in live loop

Inventory now rebuilt each tick from actual window fills (data-api) — kills the
phantom-fill bug (window 2). A naked leg COMPLETEs into a pair (FOK buy the light
side) when pair cost < \$1, else SELLs (FOK) + suppresses. Posts are skipped on a
failed fills read. Validate the data-api response shape on the server before live."
```

---

## After all tasks

- [ ] Dispatch a final code review over the whole diff (focus: inventory can never show phantom shares; COMPLETE never assembles a pair ≥ $1; suppress only on SELL; `auto_flat=False` path byte-identical; posting skipped when inventory unknown).
- [ ] Report to the user: tests green, what changed, and that deploy + the data-api-shape validation are the gated next steps once the server SSH is back. Do NOT launch live without the user's explicit "go".
- [ ] Use superpowers:finishing-a-development-branch.

## Self-Review notes (author)

- **Spec coverage:** real-fills inventory (Tasks 2,3,5) ✓; `plan_naked_action` COMPLETE/SELL (Task 1) ✓; suppress only on SELL (Tasks 4,5) ✓; grace+near_end backstop preserved (Task 5) ✓; error → reuse last inv + skip posts (Task 5 Steps 2,4) ✓; sim proof incl window-2 & window-3 (Tasks 2,4) ✓.
- **Deviation logged:** data-api `/activity` instead of CLOB `get_trades` (verified shape; validation gate in Task 5 Step 6).
- **Type consistency:** fill dict `{side, action, size, price}`, `Inventory.inv/cost/avg()`, `NakedAction(kind, side, qty)`, `fetch_window_fills(funder, slug, http)` — consistent across Tasks 1–5.
- **Retirement:** `LocalInventory`/`plan_flatten`/`debit_fill` no longer used by the ladder path; `LocalInventory` kept for legacy `_requote_window`. `debit_fill` (added by the prior feature) is now dead in the ladder but harmless; left in place.
- **YAGNI:** no get_trades, no WS fills, no legacy-path changes.

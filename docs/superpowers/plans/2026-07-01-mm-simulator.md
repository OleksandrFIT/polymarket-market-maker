# Market-Maker Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, tape-replay market-maker simulator — calibrated to reproduce competitor `0xb27b`'s real per-window positions — that outputs an honest expected $/day at our capital, with zero live trading.

**Architecture:** Pure, independently-testable units under a new `quoter/research/` package: shared types → fill model → quoting policies → window simulator → calibrator, plus a thin I/O tape loader and a report script. Sim replays each real taker-trade tape, fills our resting bids piecemeal (queue-haircut), merges matched pairs, holds residual to resolution. Calibration grid-searches queue/lag params so the sim reproduces the competitor's real fills; the report then runs the calibrated sim with our (capped) policy.

**Tech Stack:** Python 3.13, stdlib only (`dataclasses`, `urllib`, `json`, `statistics`), pytest. No new dependencies. Follows repo convention (pure planners + unit tests, `.venv/bin/python`).

**Side naming (used everywhere):** `"Up"` and `"Down"`. Tape `outcomeIndex` 0 → `"Up"`, 1 → `"Down"`.

**Tape trade record (normalized dict):** `{"ts": int, "side": "BUY"|"SELL", "oi": 0|1, "price": float, "size": float}` where `side` is the TAKER's side.

---

### Task 1: Shared types

**Files:**
- Create: `quoter/research/__init__.py`
- Create: `quoter/research/mm_types.py`
- Test: `tests/test_mm_types.py`

- [ ] **Step 1: Create the package init**

Create `quoter/research/__init__.py` (empty file).

- [ ] **Step 2: Write the failing test**

`tests/test_mm_types.py`:

```python
from quoter.research.mm_types import Quote, Theta, FillResult, WindowResult


def test_quote_holds_side_price_size():
    q = Quote("Up", 0.42, 5.0)
    assert (q.side, q.price, q.size) == ("Up", 0.42, 5.0)


def test_theta_defaults_lag_zero():
    t = Theta(fill=0.3)
    assert t.fill == 0.3 and t.lag == 0.0


def test_fillresult_and_windowresult_fields():
    fr = FillResult(filled=3.0, avg_price=0.42)
    assert fr.filled == 3.0 and fr.avg_price == 0.42
    wr = WindowResult(gross_up=1, gross_dn=2, avg_up=0.4, avg_dn=0.5,
                      pair_cost=0.9, spent=3.0, returned=3.5, pnl=0.5, adverse=0.0)
    assert wr.pnl == 0.5 and wr.pair_cost == 0.9
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mm_types.py -q`
Expected: FAIL (ModuleNotFoundError: quoter.research.mm_types).

- [ ] **Step 4: Write the implementation**

`quoter/research/mm_types.py`:

```python
"""Shared value types for the offline market-maker simulator (quoter/research)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Quote:
    side: str          # "Up" | "Down"
    price: float       # our resting BID price (we only ever buy — never sell)
    size: float        # shares


@dataclass(frozen=True)
class Theta:
    """Calibrated fill params. `fill` = fraction of crossing taker volume we capture
    (encodes queue depth ahead of us). `lag` = seconds a quote stays exposed to the tape
    after a refresh tick (cancel latency; applied by the simulator when slicing the tape)."""
    fill: float
    lag: float = 0.0


@dataclass
class FillResult:
    filled: float
    avg_price: float   # a maker bid fills at its OWN price, so this equals the quote price


@dataclass
class WindowResult:
    gross_up: float    # total shares filled on Up over the window (before merge removal)
    gross_dn: float
    avg_up: float      # cost-weighted avg fill price, Up
    avg_dn: float
    pair_cost: float   # avg_up + avg_dn (matched-pair cost; <1 => locked spread on merge)
    spent: float       # total USDC out
    returned: float    # merge redemptions + winner redemptions at resolution
    pnl: float         # returned - spent
    adverse: float     # loser shares held to resolution (lost their cost)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mm_types.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add quoter/research/__init__.py quoter/research/mm_types.py tests/test_mm_types.py
git commit -m "feat(mm-sim): shared value types (Quote/Theta/FillResult/WindowResult)"
```

---

### Task 2: Fill model (pure core)

**Files:**
- Create: `quoter/research/mm_fill.py`
- Test: `tests/test_mm_fill.py`

**Interface:** `fill(side, price, size, tape_slice, theta) -> FillResult`. A resting BID on `side` at `price` for `size` shares fills from taker SELLs of that side printing at price ≤ `price`, capturing `theta.fill` of each crossing trade's volume until size is exhausted. (`theta.lag` is NOT used here — the simulator applies it by choosing the tape slice.)

- [ ] **Step 1: Write the failing tests**

`tests/test_mm_fill.py`:

```python
from quoter.research.mm_fill import fill
from quoter.research.mm_types import Theta


def T(ts, side, oi, price, size):
    return {"ts": ts, "side": side, "oi": oi, "price": price, "size": size}


def test_bid_fills_from_crossing_taker_sell():
    # bid Up @0.50, taker SELLs Up 10 @0.48 (<=0.50) -> we capture fill_frac of 10
    r = fill("Up", 0.50, 100, [T(1, "SELL", 0, 0.48, 10)], Theta(fill=1.0))
    assert r.filled == 10.0
    assert r.avg_price == 0.50          # maker fills at OUR price


def test_no_fill_when_trade_price_above_bid():
    r = fill("Up", 0.50, 100, [T(1, "SELL", 0, 0.55, 10)], Theta(fill=1.0))
    assert r.filled == 0.0


def test_wrong_side_no_fill():
    # a SELL of Down does not fill our Up bid
    r = fill("Up", 0.50, 100, [T(1, "SELL", 1, 0.40, 10)], Theta(fill=1.0))
    assert r.filled == 0.0


def test_taker_buy_does_not_fill_our_bid():
    # our BID needs a taker SELL; a taker BUY hits asks, not our bid
    r = fill("Up", 0.50, 100, [T(1, "BUY", 0, 0.48, 10)], Theta(fill=1.0))
    assert r.filled == 0.0


def test_queue_haircut_fill_frac():
    r = fill("Up", 0.50, 100, [T(1, "SELL", 0, 0.48, 10)], Theta(fill=0.3))
    assert abs(r.filled - 3.0) < 1e-9


def test_capped_at_our_size():
    r = fill("Up", 0.50, 5, [T(1, "SELL", 0, 0.48, 100)], Theta(fill=1.0))
    assert r.filled == 5.0


def test_accumulates_across_multiple_crossings():
    tape = [T(1, "SELL", 0, 0.49, 4), T(2, "SELL", 0, 0.47, 4)]
    r = fill("Up", 0.50, 100, tape, Theta(fill=1.0))
    assert r.filled == 8.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mm_fill.py -q`
Expected: FAIL (ModuleNotFoundError: quoter.research.mm_fill).

- [ ] **Step 3: Write the implementation**

`quoter/research/mm_fill.py`:

```python
"""Pure maker-fill model for the MM simulator. A resting BID fills from crossing taker
SELLs, queue-haircut by theta.fill. We NEVER post asks (the tactic never sells)."""
from __future__ import annotations

from quoter.research.mm_types import FillResult, Theta

_OI = {0: "Up", 1: "Down"}


def fill(side: str, price: float, size: float, tape_slice: list, theta: Theta) -> FillResult:
    remaining = float(size)
    filled = 0.0
    for t in tape_slice:
        if remaining <= 0:
            break
        if t["side"] != "SELL":
            continue
        if _OI.get(t["oi"]) != side:
            continue
        if t["price"] > price:
            continue
        take = min(remaining, float(t["size"]) * theta.fill)
        if take <= 0:
            continue
        filled += take
        remaining -= take
    return FillResult(filled=filled, avg_price=price if filled > 0 else 0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mm_fill.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_fill.py tests/test_mm_fill.py
git commit -m "feat(mm-sim): pure maker-fill model (crossing taker SELLs, queue haircut)"
```

---

### Task 3: Quoting policies (pure)

**Files:**
- Create: `quoter/research/mm_policy.py`
- Test: `tests/test_mm_policy.py`

**Interface:** two functions returning `list[Quote]`, both BIDS only.
- `guru_like_quotes(mid, size, levels) -> list[Quote]`: `levels` small bids per side, laddered 1¢ apart starting 1¢ below each side's implied price (Up price = mid, Down price = 1-mid). Skips prices ≤ 0. Full-book style for calibration.
- `our_quotes(mid, size, levels, spread, inventory) -> list[Quote]`: capped policy — `levels` bids per side starting `spread` below the side's price, 1¢ apart; inventory-skew halves size on a side once its inventory exceeds `size*levels`. `inventory` is `{"Up": float, "Down": float}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mm_policy.py`:

```python
from quoter.research.mm_policy import guru_like_quotes, our_quotes


def test_guru_like_quotes_both_sides_laddered():
    qs = guru_like_quotes(mid=0.50, size=5, levels=2)
    ups = [q for q in qs if q.side == "Up"]
    dns = [q for q in qs if q.side == "Down"]
    assert len(ups) == 2 and len(dns) == 2
    assert [q.price for q in ups] == [0.49, 0.48]      # 1c below mid, laddered
    assert [q.price for q in dns] == [0.49, 0.48]      # Down price = 1-0.50 = 0.50
    assert all(q.size == 5 for q in qs)


def test_guru_like_skips_nonpositive_prices():
    qs = guru_like_quotes(mid=0.005, size=5, levels=3)   # Up price 0.005 -> ladder goes <=0
    assert all(q.price > 0 for q in qs)


def test_our_quotes_spread_and_levels():
    qs = our_quotes(mid=0.50, size=5, levels=2, spread=0.02, inventory={"Up": 0, "Down": 0})
    ups = [q for q in qs if q.side == "Up"]
    assert [round(q.price, 3) for q in ups] == [0.48, 0.47]   # spread below, then 1c
    assert all(q.size == 5 for q in qs)


def test_our_quotes_inventory_skew_halves_heavy_side():
    inv = {"Up": 100, "Down": 0}     # Up heavy (> size*levels = 5*2 = 10)
    qs = our_quotes(mid=0.50, size=5, levels=2, spread=0.02, inventory=inv)
    up_sizes = [q.size for q in qs if q.side == "Up"]
    dn_sizes = [q.size for q in qs if q.side == "Down"]
    assert all(s == 2.5 for s in up_sizes)     # halved
    assert all(s == 5 for s in dn_sizes)       # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mm_policy.py -q`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Write the implementation**

`quoter/research/mm_policy.py`:

```python
"""Pure quoting policies for the MM simulator. All quotes are BIDS (buy) on both sides —
the tactic never sells. `guru_like` is the wide full-book policy used for calibration;
`our` is the capped policy we would actually run."""
from __future__ import annotations

from quoter.research.mm_types import Quote


def guru_like_quotes(mid: float, size: float, levels: int) -> list[Quote]:
    out: list[Quote] = []
    for base, side in ((mid, "Up"), (1 - mid, "Down")):
        for k in range(levels):
            p = round(base - 0.01 * (k + 1), 3)
            if p > 0:
                out.append(Quote(side, p, float(size)))
    return out


def our_quotes(mid: float, size: float, levels: int, spread: float,
               inventory: dict) -> list[Quote]:
    out: list[Quote] = []
    heavy_threshold = size * levels
    for base, side in ((mid, "Up"), (1 - mid, "Down")):
        sz = float(size) * (0.5 if inventory.get(side, 0.0) > heavy_threshold else 1.0)
        for k in range(levels):
            p = round(base - spread - 0.01 * k, 3)
            if p > 0:
                out.append(Quote(side, p, sz))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mm_policy.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_policy.py tests/test_mm_policy.py
git commit -m "feat(mm-sim): pure quoting policies (guru_like + capped ours, inventory-skew)"
```

---

### Task 4: Window simulator (pure)

**Files:**
- Create: `quoter/research/mm_sim.py`
- Test: `tests/test_mm_sim.py`

**Interface:** `simulate_window(tape, winner, policy_fn, theta, ticks) -> WindowResult`.
- `tape`: normalized trades sorted by `ts`.
- `winner`: `"Up"` or `"Down"`.
- `policy_fn`: callable `(mid, inventory) -> list[Quote]` (bind size/levels/etc. with a lambda/partial before passing).
- `ticks`: list of `(ts, mid)` sample points (when we refresh quotes).
- Logic: at tick i, expose quotes to tape slice `[ts_i, ts_{i+1} + theta.lag)` (last tick's end = last trade ts + 1). For each quote, `fill(...)`; add to gross inventory/cost and `spent`. After each tick, MERGE matched pairs: `m = min(inv_up, inv_dn)`; `returned += m`; remove `m` from each live inventory (but gross totals keep the full history); reduce live cost proportionally. At resolution: `returned += live_inv[winner]`; loser live shares expire. `pnl = returned - spent`. `adverse` = loser gross shares that were never merged (held to a losing resolution) = `live_inv[loser]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mm_sim.py`:

```python
from quoter.research.mm_sim import simulate_window
from quoter.research.mm_policy import guru_like_quotes
from quoter.research.mm_types import Theta


def T(ts, side, oi, price, size):
    return {"ts": ts, "side": side, "oi": oi, "price": price, "size": size}


def test_one_sided_winner_hold_to_resolution():
    # single Up bid @0.49 gets filled 10; Up wins -> pnl = 10*1 - 10*0.49 = 5.1
    tape = [T(10, "SELL", 0, 0.49, 10)]
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: [q for q in guru_like_quotes(mid, 10, 1) if q.side == "Up"]
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0), ticks)
    assert abs(r.gross_up - 10.0) < 1e-9
    assert abs(r.spent - 4.9) < 1e-9
    assert abs(r.pnl - 5.1) < 1e-9
    assert r.adverse == 0.0


def test_matched_pair_merges_and_locks_spread():
    # Both bids are @0.49 (guru_like: 1c below mid 0.50 on each side). A maker fills at
    # its OWN price, so both fill 10 @0.49. Merge 10 pairs: returned 10, spent 4.9+4.9=9.8
    # -> pnl = 10 - 9.8 = 0.2 regardless of winner; pair_cost = 0.49+0.49 = 0.98.
    tape = [T(10, "SELL", 0, 0.49, 10), T(11, "SELL", 1, 0.48, 10)]
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: guru_like_quotes(mid, 10, 1)
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0), ticks)
    assert abs(r.pnl - 0.2) < 1e-9
    assert abs(r.pair_cost - 0.98) < 1e-9
    assert r.adverse == 0.0


def test_naked_loser_is_adverse_loss():
    # only Down bid @0.49 fills 10 (at our price 0.49); Up wins -> Down worthless.
    # pnl = -4.9, adverse = 10
    tape = [T(10, "SELL", 1, 0.48, 10)]
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: [q for q in guru_like_quotes(mid, 10, 1) if q.side == "Down"]
    r = simulate_window(tape, "Up", pol, Theta(fill=1.0), ticks)
    assert abs(r.pnl - (-4.9)) < 1e-9
    assert abs(r.adverse - 10.0) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mm_sim.py -q`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Write the implementation**

`quoter/research/mm_sim.py`:

```python
"""Pure single-window MM simulator: replay the taker tape, fill our resting bids,
merge matched pairs, hold residual to resolution, compute PnL."""
from __future__ import annotations

from quoter.research.mm_fill import fill
from quoter.research.mm_types import Theta, WindowResult


def simulate_window(tape: list, winner: str, policy_fn, theta: Theta,
                    ticks: list) -> WindowResult:
    live = {"Up": 0.0, "Down": 0.0}       # inventory after merges
    gross = {"Up": 0.0, "Down": 0.0}      # cumulative fills (never reduced)
    gcost = {"Up": 0.0, "Down": 0.0}      # cumulative cost, for avg price
    spent = 0.0
    returned = 0.0
    last_ts = (tape[-1]["ts"] + 1) if tape else 0

    for i, (ts, mid) in enumerate(ticks):
        end = ticks[i + 1][0] if i + 1 < len(ticks) else last_ts
        sl = [t for t in tape if ts <= t["ts"] < end + theta.lag]
        for q in policy_fn(mid, live):
            fr = fill(q.side, q.price, q.size, sl, theta)
            if fr.filled > 0:
                c = fr.filled * q.price
                live[q.side] += fr.filled
                gross[q.side] += fr.filled
                gcost[q.side] += c
                spent += c
        # merge matched pairs -> each pair redeems for 1.0
        m = min(live["Up"], live["Down"])
        if m > 0:
            live["Up"] -= m
            live["Down"] -= m
            returned += m

    # resolution: winner's remaining live shares redeem at 1.0; loser expires
    returned += live[winner]
    loser = "Down" if winner == "Up" else "Up"
    adverse = live[loser]

    avg_up = gcost["Up"] / gross["Up"] if gross["Up"] else 0.0
    avg_dn = gcost["Down"] / gross["Down"] if gross["Down"] else 0.0
    return WindowResult(
        gross_up=gross["Up"], gross_dn=gross["Down"],
        avg_up=avg_up, avg_dn=avg_dn, pair_cost=avg_up + avg_dn,
        spent=spent, returned=returned, pnl=returned - spent, adverse=adverse,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mm_sim.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_sim.py tests/test_mm_sim.py
git commit -m "feat(mm-sim): pure window simulator (fill + merge + resolution PnL)"
```

---

### Task 5: Tape loader (I/O + pure normalize)

**Files:**
- Create: `quoter/research/mm_tape.py`
- Test: `tests/test_mm_tape.py`

**Interface:**
- `normalize_trades(raw) -> list[dict]` (PURE): map raw data-api trade dicts to the normalized record, sorted by `ts`. Skips records missing required fields.
- `ticks_from_tape(tape, open_ts, step_sec) -> list[(ts, mid)]` (PURE): sample points every `step_sec` from `open_ts`; `mid` = last Up trade price at/before that ts (fallback 0.5 until first Up trade).
- I/O (not unit-tested, thin): `load_window(slug)` → `(tape, winner, open_ts)` using `data-api/trades?market=<cond>` + `gamma-api/markets?slug=`; `competitor_targets(addr)` → list of per-window real fills from `data-api/positions`. Cache raw JSON to `/tmp/poly_mm_cache`.

Only `normalize_trades` and `ticks_from_tape` are unit-tested (pure); the network functions reuse patterns already validated in `scripts/_forensic_pnl_split.py` / `_profile_wallet.py`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mm_tape.py`:

```python
from quoter.research.mm_tape import normalize_trades, ticks_from_tape


def test_normalize_maps_and_sorts():
    raw = [
        {"timestamp": 20, "side": "SELL", "outcomeIndex": 1, "price": "0.4", "size": "3"},
        {"timestamp": 10, "side": "BUY", "outcomeIndex": 0, "price": "0.6", "size": "2"},
    ]
    out = normalize_trades(raw)
    assert [t["ts"] for t in out] == [10, 20]
    assert out[0] == {"ts": 10, "side": "BUY", "oi": 0, "price": 0.6, "size": 2.0}


def test_normalize_skips_incomplete():
    raw = [{"timestamp": 1, "side": "SELL"}, {"timestamp": 2, "side": "SELL",
            "outcomeIndex": 0, "price": "0.5", "size": "1"}]
    out = normalize_trades(raw)
    assert len(out) == 1 and out[0]["ts"] == 2


def test_ticks_sample_and_carry_mid():
    tape = [{"ts": 100, "side": "SELL", "oi": 0, "price": 0.55, "size": 1},
            {"ts": 160, "side": "SELL", "oi": 0, "price": 0.70, "size": 1}]
    ticks = ticks_from_tape(tape, open_ts=100, step_sec=60)
    # tick0 @100 mid=0.55; tick1 @160 mid=0.70
    assert ticks[0] == (100, 0.55)
    assert ticks[1] == (160, 0.70)


def test_ticks_fallback_mid_before_first_up_trade():
    tape = [{"ts": 130, "side": "SELL", "oi": 0, "price": 0.62, "size": 1}]
    ticks = ticks_from_tape(tape, open_ts=100, step_sec=60)
    assert ticks[0] == (100, 0.5)      # no Up trade yet at t=100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mm_tape.py -q`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Write the implementation**

`quoter/research/mm_tape.py`:

```python
"""Tape loader for the MM simulator. Pure helpers (normalize_trades, ticks_from_tape)
are unit-tested; the network loaders are thin I/O reusing validated data-api/gamma
patterns. Cache raw JSON to /tmp/poly_mm_cache."""
from __future__ import annotations

import json
import os
import time
import urllib.request

CACHE = "/tmp/poly_mm_cache"
UA = {"User-Agent": "Mozilla/5.0"}


def normalize_trades(raw: list) -> list:
    out = []
    for t in raw:
        try:
            out.append({
                "ts": int(t["timestamp"]),
                "side": t["side"],
                "oi": int(t["outcomeIndex"]),
                "price": float(t["price"]),
                "size": float(t["size"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda t: t["ts"])
    return out


def ticks_from_tape(tape: list, open_ts: int, step_sec: int) -> list:
    if not tape:
        return [(open_ts, 0.5)]
    end = tape[-1]["ts"]
    ticks = []
    up_prices = [(t["ts"], t["price"]) for t in tape if t["oi"] == 0]
    ts = open_ts
    while ts <= end:
        mid = 0.5
        for uts, up in up_prices:
            if uts <= ts:
                mid = up
            else:
                break
        ticks.append((ts, mid))
        ts += step_sec
    return ticks


# ── thin I/O (not unit-tested) ─────────────────────────────────────────────
def _get(url, tries=4):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.6)


def _cached(key, fetch):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    val = fetch()
    if val is not None:
        json.dump(val, open(path, "w"))
    return val


def load_window(slug):
    """Return (tape, winner, open_ts) for a 5m window slug, or None."""
    g = _cached("m_" + slug, lambda: _get(
        "https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug))
    if not (isinstance(g, list) and g):
        return None
    cond = g[0].get("conditionId")
    op = g[0].get("outcomePrices")
    if isinstance(op, str):
        op = json.loads(op)
    winner = "Up" if op and float(op[0]) >= 0.99 else ("Down" if op and float(op[1]) >= 0.99 else None)
    if winner is None or not cond:
        return None
    raw = _cached("t_" + slug, lambda: _fetch_trades(cond))
    tape = normalize_trades(raw or [])
    open_ts = int(slug.rsplit("-", 1)[1])
    return tape, winner, open_ts


def _fetch_trades(cond):
    out, off = [], 0
    while off < 3500:
        b = _get("https://data-api.polymarket.com/trades?market=%s&limit=500&offset=%d" % (cond, off))
        if not isinstance(b, list) or not b:
            break
        out += b
        if len(b) < 500:
            break
        off += 500
    return out


def competitor_targets(addr):
    """Real per-window end-state from open both-sided positions:
    [{slug, size_up, size_dn, avg_up, avg_dn}]."""
    r = _get("https://data-api.polymarket.com/positions?user=%s&sizeThreshold=1&limit=500" % addr)
    if not isinstance(r, list):
        return []
    byslug = {}
    for p in r:
        s = p.get("slug", "")
        if "-5m-" not in s:
            continue
        d = byslug.setdefault(s, {})
        oc = "Up" if p.get("outcome") == "Up" else "Down"
        d[oc] = (float(p.get("size", 0)), float(p.get("avgPrice", 0)))
    tgts = []
    for s, d in byslug.items():
        if "Up" in d and "Down" in d:      # both-sided => pre-merge gross visible
            tgts.append({"slug": s, "size_up": d["Up"][0], "avg_up": d["Up"][1],
                         "size_dn": d["Down"][0], "avg_dn": d["Down"][1]})
    return tgts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mm_tape.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_tape.py tests/test_mm_tape.py
git commit -m "feat(mm-sim): tape loader (pure normalize/ticks + thin data-api/gamma I/O)"
```

---

### Task 6: Calibrator

**Files:**
- Create: `quoter/research/mm_calibrate.py`
- Test: `tests/test_mm_calibrate.py`

**Interface:**
- `window_loss(result, target) -> float` (PURE): squared relative error summed over
  `size_up, size_dn, avg_up, avg_dn` (guarding divide-by-zero with a small epsilon).
- `calibrate(cal_windows, targets, policy_fn, grid) -> (best_theta, best_loss, per_theta)`:
  `cal_windows` = list of `(tape, winner, ticks)`; `targets` aligned list of dicts;
  `grid` = list of `Theta`. For each theta, sum `window_loss` over windows; return the
  argmin plus the full `{theta: loss}` map (as a list of `(theta, loss)`).

- [ ] **Step 1: Write the failing tests (incl. recovery test)**

`tests/test_mm_calibrate.py`:

```python
from quoter.research.mm_calibrate import window_loss, calibrate
from quoter.research.mm_sim import simulate_window
from quoter.research.mm_policy import guru_like_quotes
from quoter.research.mm_types import Theta, WindowResult


def _wr(su, sd, au, ad):
    return WindowResult(su, sd, au, ad, au + ad, 0, 0, 0, 0)


def test_window_loss_zero_on_exact_match():
    r = _wr(100, 200, 0.4, 0.6)
    tgt = {"size_up": 100, "size_dn": 200, "avg_up": 0.4, "avg_dn": 0.6}
    assert window_loss(r, tgt) < 1e-12


def test_window_loss_positive_on_mismatch():
    r = _wr(50, 200, 0.4, 0.6)
    tgt = {"size_up": 100, "size_dn": 200, "avg_up": 0.4, "avg_dn": 0.6}
    assert window_loss(r, tgt) > 0.0


def _synthetic_tape():
    # crossing Up + Down sells so a guru_like bid fills on both sides
    return [{"ts": 10, "side": "SELL", "oi": 0, "price": 0.48, "size": 100},
            {"ts": 11, "side": "SELL", "oi": 1, "price": 0.48, "size": 100}]


def test_calibrate_recovers_known_theta():
    # Generate a synthetic "competitor" with a KNOWN theta, then confirm calibrate finds it.
    tape = _synthetic_tape()
    ticks = [(0, 0.50)]
    pol = lambda mid, inv: guru_like_quotes(mid, 100, 1)
    theta_true = Theta(fill=0.3)
    truth = simulate_window(tape, "Up", pol, theta_true, ticks)
    target = {"size_up": truth.gross_up, "size_dn": truth.gross_dn,
              "avg_up": truth.avg_up, "avg_dn": truth.avg_dn}
    grid = [Theta(fill=f) for f in (0.1, 0.2, 0.3, 0.5, 1.0)]
    best, loss, _ = calibrate([(tape, "Up", ticks)], [target], pol, grid)
    assert best.fill == 0.3
    assert loss < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mm_calibrate.py -q`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Write the implementation**

`quoter/research/mm_calibrate.py`:

```python
"""Calibrate the fill model (Theta) so the simulator reproduces the competitor's real
per-window end-state. Grid-search; report argmin loss so the caller can apply an
honesty gate (reject projections if the best fit is poor)."""
from __future__ import annotations

from quoter.research.mm_sim import simulate_window

_EPS = 1e-6


def window_loss(result, target: dict) -> float:
    loss = 0.0
    for got, key in ((result.gross_up, "size_up"), (result.gross_dn, "size_dn"),
                     (result.avg_up, "avg_up"), (result.avg_dn, "avg_dn")):
        want = float(target[key])
        denom = abs(want) + _EPS
        loss += ((got - want) / denom) ** 2
    return loss


def calibrate(cal_windows: list, targets: list, policy_fn, grid: list):
    per_theta = []
    best = None
    for theta in grid:
        total = 0.0
        for (tape, winner, ticks), tgt in zip(cal_windows, targets):
            r = simulate_window(tape, winner, policy_fn, theta, ticks)
            total += window_loss(r, tgt)
        per_theta.append((theta, total))
        if best is None or total < best[1]:
            best = (theta, total)
    return best[0], best[1], per_theta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mm_calibrate.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_calibrate.py tests/test_mm_calibrate.py
git commit -m "feat(mm-sim): calibrator (grid-search Theta to competitor targets + recovery test)"
```

---

### Task 7: Report script (the go/no-go number)

**Files:**
- Create: `scripts/_mm_pnl.py`

This is a glue script (run manually, network). It is NOT unit-tested; it is verified by running it and reading output. It ties the pieces together:
1. Pull competitor targets (open both-sided windows) + their tapes.
2. Calibrate `Theta` to those targets with `guru_like` policy.
3. Fire the honesty gate if best-fit loss/window exceeds a threshold.
4. Run the calibrated sim with `our_quotes` at our capital/size over a recent window set.
5. Print expected $/day + breakdown + scaling curve.

- [ ] **Step 1: Write the script**

`scripts/_mm_pnl.py`:

```python
"""Go/no-go report: calibrate the MM sim to competitor 0xb27b, then project our expected
$/day at our capital. Read-only, no live trading. Usage: python3 scripts/_mm_pnl.py [addr]"""
import sys
import statistics
from functools import partial

from quoter.research.mm_tape import load_window, competitor_targets, ticks_from_tape
from quoter.research.mm_policy import guru_like_quotes, our_quotes
from quoter.research.mm_sim import simulate_window
from quoter.research.mm_calibrate import calibrate
from quoter.research.mm_types import Theta

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
STEP = 20                         # quote-refresh cadence (sec)
LOSS_GATE = 0.5                   # per-window loss above this => projection unreliable
WINDOWS_PER_DAY = 288            # 5m windows in a day

# ── 1. calibration set: competitor open both-sided windows ──
tgts = competitor_targets(ADDR)
cal = []
targets = []
for t in tgts:
    w = load_window(t["slug"])
    if not w or not w[0]:
        continue
    tape, winner, open_ts = w
    ticks = ticks_from_tape(tape, open_ts, STEP)
    cal.append((tape, winner, ticks))
    targets.append(t)
print("calibration windows: %d" % len(cal))
if not cal:
    print("no calibration windows (competitor inactive or tapes unavailable)"); sys.exit()

# ── 2. calibrate guru_like policy ──
guru_pol = lambda mid, inv: guru_like_quotes(mid, size=9, levels=8)
grid = [Theta(fill=f, lag=lag) for f in (0.05, 0.1, 0.2, 0.3, 0.5, 0.8) for lag in (0.0, 2.0)]
theta, loss, per = calibrate(cal, targets, guru_pol, grid)
per_window = loss / len(cal)
print("best theta: fill=%.2f lag=%.1f  loss/window=%.3f" % (theta.fill, theta.lag, per_window))

# ── 3. honesty gate ──
if per_window > LOSS_GATE:
    print("\nHONESTY GATE: sim cannot reproduce the competitor within tolerance")
    print("(loss/window %.3f > %.3f). Projection is UNRELIABLE — do NOT trust a $/day number."
          % (per_window, LOSS_GATE))
    sys.exit()

# ── 4. project OUR policy at OUR capital over the same tapes ──
def run_ours(size, levels, spread):
    pnls = []
    for tape, winner, ticks in cal:
        pol = lambda mid, inv, s=size, l=levels, sp=spread: our_quotes(mid, s, l, sp, inv)
        r = simulate_window(tape, winner, pol, theta, ticks)
        pnls.append(r.pnl)
    return pnls

print("\n=== OUR policy projection (calibrated theta) ===")
base = run_ours(size=5, levels=2, spread=0.02)
avg = statistics.mean(base)
print("per-window PnL: mean $%.4f  median $%.4f  n=%d" % (avg, statistics.median(base), len(base)))
print("=> expected $/day (%d win): $%.2f" % (WINDOWS_PER_DAY, avg * WINDOWS_PER_DAY))
if len(base) > 1 and statistics.pstdev(base) > 0:
    print("   Sharpe-ish (per-window): %.3f" % (avg / statistics.pstdev(base)))

# ── 5. scaling curve ──
print("\n=== scaling curve ($/day) ===")
for size in (5, 10, 25, 50):
    p = run_ours(size=size, levels=2, spread=0.02)
    print("  size %2d: $/day $%.2f  (mean/win $%.4f)" % (size, statistics.mean(p) * WINDOWS_PER_DAY, statistics.mean(p)))
```

- [ ] **Step 2: Run it (verification)**

Run: `.venv/bin/python scripts/_mm_pnl.py`
Expected: prints calibration window count, best theta, then either the HONESTY GATE message or the $/day projection + scaling curve. (Depends on live competitor data; a non-empty calibration set is required — if the competitor has no open both-sided windows at run time, it reports that and exits.)

- [ ] **Step 3: Commit**

```bash
git add scripts/_mm_pnl.py
git commit -m "feat(mm-sim): go/no-go report (calibrate to competitor, project our \$/day)"
```

---

### Task 8: Full suite green

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all prior tests + the ~21 new mm-sim tests green).

- [ ] **Step 2: Commit any fixups if needed** (only if a cross-module inconsistency surfaced).

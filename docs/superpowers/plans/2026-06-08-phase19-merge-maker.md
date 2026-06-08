# Phase-19 Two-Sided Merge-Maker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the directional strategy with a two-sided merge-maker that posts
bids on both Up and Down at pair cost < $1.00 and merges matched pairs.

**Architecture:** `compute_ladder` becomes a pure two-sided pricing function
(`_merge_ladder`); the merge action runs in the paper fill-drain path via the
existing `inventory.on_merge`. Directional + lottery config knobs are removed.

**Tech Stack:** Python 3.11, dataclasses, pytest, asyncio.

---

### Task 1: Config knobs — add merge, remove directional/lottery

**Files:**
- Modify: `quoter/config.py`
- Test: `tests/test_config.py` (if present; else skip)

- [ ] **Step 1: Add merge knobs to `Config`**, in a new `# ── Phase-19 merge-maker ──` block:
```python
    merge_edge: float = 0.01        # target total edge per Up+Down pair (per-leg = /2)
    max_naked_shares: int = 20      # hard cap on |yes_qty - no_qty|
    merge_levels: int = 2           # bids per side per tick
```

- [ ] **Step 2: Remove directional + lottery knobs** from `Config`:
`favorite_min_price`, `max_entry_price`, `entry_start_frac`, `flat_size` (KEEP
flat_size — used), `certainty_cap_multiplier`, `velocity_confirm_threshold`,
`rise_tolerance_cents`, `favorite_ladder_levels`, `momentum_velocity_threshold`,
`momentum_min_price`, `momentum_max_price`, `lottery_max_price`,
`lottery_cap_usd`, `lottery_size`, `lottery_levels`.
KEEP: `flat_size`, `per_market_cap_usd`, `min_time_to_expiry_sec`, velocity
buffer knobs, paper-fill, risk, WS, paths, endpoints.

- [ ] **Step 3: Run config import + any config test**
Run: `.venv/bin/python -c "from quoter.config import Config; c=Config(); print(c.merge_edge, c.max_naked_shares, c.merge_levels, c.flat_size)"`
Expected: `0.01 20 2 10`

- [ ] **Step 4: Commit**
```bash
git add quoter/config.py && git commit -m "feat(phase-19): config merge knobs; drop directional/lottery knobs"
```

---

### Task 2: Rewrite `ladder.py` to two-sided merge-maker (TDD)

**Files:**
- Modify: `quoter/strategy/ladder.py`
- Test: `tests/test_ladder.py` (rewrite)

- [ ] **Step 1: Write the failing tests** in `tests/test_ladder.py` (replace file).
Cover, using a `Config` built with explicit merge knobs:
```python
from quoter.config import Config
from quoter.strategy.ladder import compute_ladder, Quote

def cfg(**kw):
    return Config(merge_edge=0.01, max_naked_shares=20, merge_levels=2,
                  flat_size=10, per_market_cap_usd=50.0, min_time_to_expiry_sec=5.0, **kw)

def best(bids, side):
    ps = [b.price for b in bids if b.side == side]
    return max(ps) if ps else None

def test_posts_both_legs():
    b = compute_ladder(cfg(), mid_yes=0.55, time_to_expiry=60)
    assert best(b, "YES") is not None and best(b, "NO") is not None

def test_pair_below_one():
    b = compute_ladder(cfg(), mid_yes=0.55, time_to_expiry=60)
    assert best(b, "YES") + best(b, "NO") < 1.0

def test_no_edge_skips():
    # merge_edge tiny enough that rounding kills the edge -> empty
    b = compute_ladder(cfg(merge_edge=0.0), mid_yes=0.50, time_to_expiry=60)
    assert b == []

def test_balance_gate_suppresses_long_side():
    b = compute_ladder(cfg(), mid_yes=0.50, time_to_expiry=60,
                       inventory_yes_qty=30, inventory_no_qty=0)
    assert best(b, "YES") is None and best(b, "NO") is not None

def test_per_market_cap_stops():
    b = compute_ladder(cfg(per_market_cap_usd=1.0), mid_yes=0.55, time_to_expiry=60,
                       inventory_yes_qty=10, inventory_no_qty=10,
                       inventory_yes_cost=5.0, inventory_no_cost=5.0)
    assert b == []

def test_extreme_mid_skips_leg():
    b = compute_ladder(cfg(), mid_yes=0.99, time_to_expiry=60)
    # NO leg price = (1-0.99)-0.005 = 0.005 -> rounds to 0.01 or 0.0; ensure no negative/zero-price bids
    assert all(q.price > 0 for q in b)

def test_merge_edge_widens_spread():
    narrow = compute_ladder(cfg(merge_edge=0.01), mid_yes=0.55, time_to_expiry=60)
    wide   = compute_ladder(cfg(merge_edge=0.04), mid_yes=0.55, time_to_expiry=60)
    assert best(wide, "YES") < best(narrow, "YES")

def test_time_to_expiry_gate():
    assert compute_ladder(cfg(), mid_yes=0.55, time_to_expiry=1) == []

def test_levels_count():
    b = compute_ladder(cfg(merge_levels=3), mid_yes=0.55, time_to_expiry=60)
    assert sum(1 for q in b if q.side == "YES") == 3
```

- [ ] **Step 2: Run tests, verify they fail**
Run: `.venv/bin/python -m pytest tests/test_ladder.py -q`
Expected: failures (old `compute_ladder` signature / behavior).

- [ ] **Step 3: Rewrite `ladder.py`**:
```python
"""Phase-19 strategy: two-sided merge-maker.

Pure function: compute_ladder(cfg, mid_yes, time_to_expiry, ...) -> list[Quote].
Posts maker bids on BOTH outcomes at a target pair cost < $1.00 (Up @ mid-δ,
Down @ (1-mid)-δ). The matched pairs are merged to $1.00 by the caller, locking
the spread. A balance gate caps naked (one-sided) exposure.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from quoter.config import Config

Side = Literal["YES", "NO"]


@dataclass
class Quote:
    side: Side
    price: float
    size: int


def _ladder(side: Side, top_price: float, size: int, levels: int) -> list[Quote]:
    out: list[Quote] = []
    for i in range(levels):
        p = round(top_price - i * 0.01, 2)
        if p <= 0.0:
            continue
        out.append(Quote(side, p, size))
    return out


def _merge_ladder(
    cfg: Config, mid_yes: float,
    yes_qty: int, no_qty: int, yes_cost: float, no_cost: float,
) -> list[Quote]:
    delta = cfg.merge_edge / 2.0
    up_price = round(mid_yes - delta, 2)
    dn_price = round((1.0 - mid_yes) - delta, 2)
    if up_price + dn_price >= 1.0:          # Gate 1: edge gone after rounding
        return []
    if (yes_cost + no_cost) >= cfg.per_market_cap_usd:   # Gate 2: capital
        return []
    cap = cfg.max_naked_shares              # Gate 3: balance
    post_up = (yes_qty - no_qty) < cap
    post_down = (no_qty - yes_qty) < cap
    bids: list[Quote] = []
    if post_up and up_price > 0.0:
        bids += _ladder("YES", up_price, cfg.flat_size, cfg.merge_levels)
    if post_down and dn_price > 0.0:
        bids += _ladder("NO", dn_price, cfg.flat_size, cfg.merge_levels)
    return bids


def compute_ladder(
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    *,
    inventory_yes_qty: int = 0,
    inventory_no_qty: int = 0,
    inventory_yes_cost: float = 0.0,
    inventory_no_cost: float = 0.0,
    **_legacy,   # tolerate/ignore any leftover caller kwargs during transition
) -> list[Quote]:
    if not (0.02 <= mid_yes <= 0.99) or time_to_expiry < cfg.min_time_to_expiry_sec:
        return []
    return _merge_ladder(
        cfg, mid_yes,
        inventory_yes_qty, inventory_no_qty,
        inventory_yes_cost, inventory_no_cost,
    )
```
(Remove `_momentum_leg`, `_lottery_leg`, `_favorite_ladder`.)

- [ ] **Step 4: Run tests, verify pass**
Run: `.venv/bin/python -m pytest tests/test_ladder.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**
```bash
git add quoter/strategy/ladder.py tests/test_ladder.py
git commit -m "feat(phase-19): two-sided merge-maker compute_ladder"
```

---

### Task 3: quoter_loop — simplify call + merge step (TDD)

**Files:**
- Modify: `quoter/quoter_loop.py`
- Test: `tests/test_quoter_loop.py` (add merge test; fix call if asserted)

- [ ] **Step 1: Write a failing test** that after balanced paper fills the merge runs:
```python
def test_apply_paper_fills_merges_matched():
    # build a QuoterLoop with a stub PaperExecutor returning one YES and one NO
    # fill of equal qty; after _apply_paper_fills, inv.n_merges >= 1 and the
    # matched pairs are gone (yes_qty==no_qty==0 after merge of equal sizes).
    ...
```
(Use existing test harness/fixtures in the file for constructing the loop; mirror
the style already present.)

- [ ] **Step 2: Run it, verify failure**
Run: `.venv/bin/python -m pytest tests/test_quoter_loop.py -q -k merge`

- [ ] **Step 3: Simplify the `compute_ladder` call** (lines ~218–232) to:
```python
            eff_cfg = replace(self.cfg, **self.live.snapshot())
            pos = self.inv.positions.get(market_id)
            desired = compute_ladder(
                eff_cfg,
                mid_yes=mid_yes,
                time_to_expiry=tte,
                inventory_yes_qty=pos.yes_qty if pos else 0,
                inventory_no_qty=pos.no_qty if pos else 0,
                inventory_yes_cost=pos.yes_cost_total if pos else 0.0,
                inventory_no_cost=pos.no_cost_total if pos else 0.0,
            )
            self.exec.sync(market_id, desired)
```
Remove the now-unused `velo_short`/`velo_long` fetch, `committed`,
`_prev_mid_yes` write (and its dict + cleanup in `remove_market` if fully
unused). Keep the velocity-buffer wiring at construction (harmless).

- [ ] **Step 4: Add the merge step** to `_apply_paper_fills`, after the fill loop:
```python
        if fills:
            pos = self.inv.positions.get(market_id)
            if pos is not None and pos.matched > 0:
                pairs = pos.matched
                self.inv.on_merge(market_id, pairs)
                log.info("paper_merge", market=market_id[:12], pairs=pairs)
```

- [ ] **Step 5: Run loop tests + full strategy tests**
Run: `.venv/bin/python -m pytest tests/test_quoter_loop.py tests/test_ladder.py -q`
Expected: pass.

- [ ] **Step 6: Commit**
```bash
git add quoter/quoter_loop.py tests/test_quoter_loop.py
git commit -m "feat(phase-19): merge matched pairs in paper drain; simplify ladder call"
```

---

### Task 4: phase-A whitelist + dashboard knob set

**Files:**
- Modify: `quoter/ops/live_settings.py`, `quoter/ops/dashboard.py`
- Test: `tests/test_live_settings.py`

- [ ] **Step 1: Update `_SPEC`** in `live_settings.py`: remove every directional +
lottery key; add:
```python
    "merge_edge": (float, 0.002, 0.04),
    "max_naked_shares": (int, 0, 200),
    "merge_levels": (int, 1, 5),
```
Keep any still-valid keys. Update `_band_warning` (drop momentum band warning;
no cross-field warning needed now, or warn if merge_edge <= 0).

- [ ] **Step 2: Update `SETTING_KEYS`** JS array in `dashboard.py` to exactly the
new editable set (merge_edge, max_naked_shares, merge_levels, flat_size,
per_market_cap_usd, min_time_to_expiry_sec — whatever `_SPEC` now exposes).

- [ ] **Step 3: Fix/extend `tests/test_live_settings.py`** for the new keys
(valid update, out-of-range clamp/reject, removed-key rejected).

- [ ] **Step 4: Run tests**
Run: `.venv/bin/python -m pytest tests/test_live_settings.py -q`
Expected: pass.

- [ ] **Step 5: Commit**
```bash
git add quoter/ops/live_settings.py quoter/ops/dashboard.py tests/test_live_settings.py
git commit -m "feat(phase-19): live-editable merge knobs; drop directional knobs from dashboard"
```

---

### Task 5: Sweep removed-knob references + full suite green

**Files:**
- Modify: `quoter/backtest/engine.py`, `quoter/backtest/run_backtest.py`,
  `tests/test_backtest_engine.py`, any other file referencing a removed knob.

- [ ] **Step 1: Grep for removed knobs**
Run: `grep -rn "momentum_\|lottery_\|favorite_\|entry_start_frac\|velocity_confirm\|certainty_\|rise_tolerance\|max_entry_price\|favorite_ladder_levels" quoter/ tests/`
Fix each: backtest must call the new `compute_ladder` signature (pass inventory
qty + cost, mid, tte); drop any sweep over removed knobs.

- [ ] **Step 2: Update `tests/test_backtest_engine.py`** — remove momentum/lottery
scenarios; assert the engine runs `compute_ladder` and posts two-sided bids (or
the no-fill behavior under the offline fill model). Keep it descriptive.

- [ ] **Step 3: Run the FULL suite**
Run: `.venv/bin/python -m pytest -q`
Expected: all green, zero references to removed knobs.

- [ ] **Step 4: Commit**
```bash
git add -A && git commit -m "chore(phase-19): purge directional-knob references; full suite green"
```

---

## Self-review notes
- Signature consistency: `compute_ladder(cfg, mid_yes, time_to_expiry, *, inventory_yes_qty, inventory_no_qty, inventory_yes_cost, inventory_no_cost)` used identically in tests, quoter_loop, and backtest.
- `flat_size` is KEPT (Task 1 Step 2 explicitly preserves it).
- Merge step guarded by `pos.matched > 0`; `on_merge` is idempotent-safe (caps at matched).
- Paper success = bounded naked + merges firing, NOT profit (per spec §6).

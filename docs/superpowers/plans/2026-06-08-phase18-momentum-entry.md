# Phase-18 Momentum-Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Replace the favorite leg with a velocity-driven `_momentum_leg` that buys the
momentum-favored side while cheap (0.40–0.65) — the proven cost-basis fix. Paper only.

**Architecture:** `compute_ladder` = `_momentum_leg(...) + _lottery_leg(...)`. Remove
`_favorite_leg`, `_certainty`, `_pick_favorite_side`. Keep `_favorite_ladder`, `_lottery_leg`.
3 new config knobs (live-editable). Backtest can't test momentum (velocity None offline) —
documented limitation.

**Tech:** Python 3.13, pytest. Test: `.venv/bin/pytest <path> -v`. Branch: master.
**Spec:** `docs/superpowers/specs/2026-06-08-phase18-momentum-entry-design.md`

---

## Task 1: Config knobs + phase-A whitelist + dashboard

**Files:** Modify `quoter/config.py`; `quoter/ops/live_settings.py`; `quoter/ops/dashboard.py`;
`tests/test_config.py`.

- [ ] **Step 1: test (`tests/test_config.py`)** — add to the defaults test (the method asserting
  config defaults, currently `test_phase17_defaults`) these three asserts:
```python
        assert c.momentum_velocity_threshold == 0.001
        assert c.momentum_min_price == 0.40
        assert c.momentum_max_price == 0.65
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_config.py -v` → FAIL.

- [ ] **Step 3: `quoter/config.py`** — add after the Phase-17 lottery block:
```python
    # ── Phase-18 momentum entry (cost-basis fix: buy velocity-favored side while cheap) ──
    momentum_velocity_threshold: float = 0.001  # min |Binance velocity| to trigger an entry
    momentum_min_price: float = 0.40            # don't buy below (too uncertain)
    momentum_max_price: float = 0.65            # don't buy above (missed cheap entry → -EV)
```

- [ ] **Step 4: `quoter/ops/live_settings.py`** — add to `_SPEC`:
```python
    "momentum_velocity_threshold": (float, 0.0, 0.02),
    "momentum_min_price": (float, 0.20, 0.60),
    "momentum_max_price": (float, 0.50, 0.90),
```

- [ ] **Step 5: `quoter/ops/dashboard.py`** — append to `SETTING_KEYS`:
```javascript
  "momentum_velocity_threshold","momentum_min_price","momentum_max_price"
```

- [ ] **Step 6:** Run `.venv/bin/pytest tests/test_config.py tests/test_live_settings.py -v` → PASS.

- [ ] **Step 7: Commit.**
```bash
git add quoter/config.py quoter/ops/live_settings.py quoter/ops/dashboard.py tests/test_config.py
git commit -m "feat(phase-18): momentum config knobs + phase-A whitelist + dashboard fields"
```

---

## Task 2: ladder.py — replace favorite leg with `_momentum_leg`

**Files:** Modify `quoter/strategy/ladder.py`; Rewrite `tests/test_ladder.py`.

- [ ] **Step 1: Rewrite `tests/test_ladder.py`** with (keep the lottery tests; replace all
  favorite tests with momentum tests):
```python
"""Tests for phase-18 momentum-entry compute_ladder (+ cheap-tail lottery)."""

from dataclasses import replace
from quoter.config import Config
from quoter.strategy.ladder import compute_ladder

LATE_TTE = 60.0
VUP = 0.01    # strong up velocity (>= momentum_velocity_threshold 0.001)
VDN = -0.01   # strong down velocity


def _favbids(out, cfg=None):
    cfg = cfg or Config()
    return [q for q in out if q.price >= cfg.momentum_min_price]


def test_no_velocity_no_momentum():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=None)
    assert _favbids(out) == []  # no momentum bids without a signal


def test_weak_velocity_no_momentum():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=0.0001)
    assert _favbids(out) == []


def test_momentum_buys_up_side():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=VUP)
    fav = _favbids(out)
    assert fav and all(q.side == "YES" for q in fav)


def test_momentum_buys_down_side():
    # velocity down → buy NO; underdog NO price = 1-0.45 = 0.55, in band
    out = compute_ladder(Config(), mid_yes=0.45, time_to_expiry=LATE_TTE, velocity_short=VDN)
    fav = _favbids(out)
    assert fav and all(q.side == "NO" for q in fav)


def test_momentum_skips_too_expensive():
    # up velocity but YES already 0.80 > momentum_max_price 0.65 → no momentum bids
    out = compute_ladder(Config(), mid_yes=0.80, time_to_expiry=LATE_TTE, velocity_short=VUP)
    assert _favbids(out) == []


def test_momentum_skips_too_cheap():
    # up velocity but YES only 0.30 < momentum_min_price 0.40 → no momentum bids
    out = compute_ladder(Config(), mid_yes=0.30, time_to_expiry=LATE_TTE, velocity_short=VUP)
    assert all(q.side != "YES" or q.price < Config().momentum_min_price for q in out)


def test_momentum_commit_one_side():
    # hold YES big ($ > max_lottery) → a down-velocity NO momentum must be blocked
    out = compute_ladder(Config(), mid_yes=0.45, time_to_expiry=LATE_TTE, velocity_short=VDN,
                         inventory_yes_qty=40)
    assert not any(q.side == "NO" and q.price >= Config().momentum_min_price for q in out)


def test_momentum_cap_stops():
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=VUP,
                         inventory_yes_qty=2000)
    assert _favbids(out) == []


def test_momentum_flat_size():
    cfg = Config()
    out = compute_ladder(cfg, mid_yes=0.55, time_to_expiry=LATE_TTE, velocity_short=VUP)
    fav = _favbids(out, cfg)
    assert fav and all(q.size == cfg.flat_size for q in fav)


# ── Lottery leg (unchanged) ──
def test_lottery_still_fires():
    # mid 0.90 → underdog NO price 0.10 <= lottery_max_price → lottery NO bids
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=None)
    assert any(q.side == "NO" and q.price <= Config().lottery_max_price for q in out)


def test_lottery_size_zero_disables():
    out = compute_ladder(replace(Config(), lottery_size=0), mid_yes=0.90,
                         time_to_expiry=LATE_TTE, velocity_short=None)
    assert out == []  # no momentum (no velocity) and no lottery
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_ladder.py -v` → FAIL.

- [ ] **Step 3: Edit `quoter/strategy/ladder.py`.**
  (a) DELETE the functions `_favorite_leg`, `_certainty`, and `_pick_favorite_side` entirely.
  (b) ADD `_momentum_leg`:
```python
def _momentum_leg(
    cfg: Config,
    mid_yes: float,
    velocity_short: float | None,
    inventory_yes_qty: int,
    inventory_no_qty: int,
) -> list[Quote]:
    """Buy the side Binance momentum favors WHILE STILL CHEAP (cost-basis fix).

    Side comes from the velocity sign (not the current favorite); we only buy in
    the cheap band [momentum_min_price, momentum_max_price] so the average cost
    basis stays low. No signal (incl. backtest velocity=None) → no bids.
    """
    if velocity_short is None or abs(velocity_short) < cfg.momentum_velocity_threshold:
        return []
    side: Side = "YES" if velocity_short > 0 else "NO"
    price = round(mid_yes, 2) if side == "YES" else round(1.0 - mid_yes, 2)
    if not (cfg.momentum_min_price <= price <= cfg.momentum_max_price):
        return []

    # Commit-to-one-side ($-value threshold, robust to lottery — same as phase-17).
    max_lottery_usd = cfg.lottery_cap_usd + cfg.lottery_size * cfg.lottery_levels * cfg.lottery_max_price
    yes_committed = inventory_yes_qty * mid_yes > max_lottery_usd
    no_committed = inventory_no_qty * (1.0 - mid_yes) > max_lottery_usd
    if yes_committed and side == "NO":
        return []
    if no_committed and side == "YES":
        return []

    # Per-market cap (flat).
    qty = inventory_yes_qty if side == "YES" else inventory_no_qty
    if qty * price >= cfg.per_market_cap_usd:
        return []

    return _favorite_ladder(side, price, cfg.flat_size, cfg)
```
  (c) UPDATE `compute_ladder`: replace the favorite-leg call with the momentum leg. The new body:
```python
    """Momentum entry (velocity-favored side, bought cheap) + cheap-tail lottery."""
    if not (0.02 <= mid_yes <= 0.99) or time_to_expiry < cfg.min_time_to_expiry_sec:
        return []
    momentum = _momentum_leg(
        cfg, mid_yes, velocity_short, inventory_yes_qty, inventory_no_qty,
    )
    lottery = _lottery_leg(cfg, mid_yes, inventory_yes_qty, inventory_no_qty)
    return momentum + lottery
```
  (compute_ladder keeps its full signature — prev_mid_yes/timeframe/window_length_sec/
  committed_side/velocity_long are still accepted for caller compatibility, now unused by the
  momentum leg.) Update the module docstring to describe momentum entry.

- [ ] **Step 4:** Run `.venv/bin/pytest tests/test_ladder.py tests/test_config.py -v` → PASS.
  (test_backtest_engine may shift — fixed in Task 3.)

- [ ] **Step 5: Commit.**
```bash
git add quoter/strategy/ladder.py tests/test_ladder.py
git commit -m "feat(phase-18): replace favorite leg with velocity-driven momentum entry"
```

---

## Task 3: Backtest limitation test + report

**Files:** Modify `tests/test_backtest_engine.py`; create `reports/2026-06-08_phase18-momentum.md`.

- [ ] **Step 1: Update `tests/test_backtest_engine.py`** — the backtest passes velocity=None, so
  the momentum leg never fires. Replace `test_buying_winning_favorite_is_profitable` with a test
  documenting the limitation (and keep the no-fill test):
```python
def test_momentum_inert_in_backtest_without_velocity():
    cfg = Config()
    # Backtest passes velocity_short=None → momentum leg never fires. With a mid that
    # gives no cheap underdog either (0.55 → underdog 0.45 > lottery_max_price 0.40),
    # the engine produces no fills. Documents that phase-18 cannot be backtested offline.
    series = [PricePoint(120, 0.55), PricePoint(180, 0.55)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0 and res.no_qty == 0
    assert res.pnl == 0.0
```
  Keep `test_no_favorite_no_fills_no_pnl` (rename allowed); verify it still asserts zero.

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_backtest_engine.py tests/test_backtest_runner.py -v`
  → PASS.

- [ ] **Step 3:** Run the FULL suite `.venv/bin/pytest -q` → ALL green.

- [ ] **Step 4: Write `reports/2026-06-08_phase18-momentum.md`** stating: the cost-basis finding
  (our 0.81 vs his 0.54, same hit-rate, +EV vs -EV math); what phase-18 changes (momentum entry,
  cheap band); and the HARD limitation that the backtest cannot evaluate it (velocity None
  offline) so paper is the only validation, plus the honest caveat that the velocity-predicts-
  winner hypothesis is unproven.

- [ ] **Step 5: Commit.**
```bash
git add tests/test_backtest_engine.py reports/2026-06-08_phase18-momentum.md
git commit -m "test+docs(phase-18): backtest-inert-without-velocity test + report"
```

---

## Self-Review
- **Spec coverage:** momentum leg replaces favorite (Task 2), 3 knobs + whitelist + dashboard
  (Task 1), backtest limitation documented + tested (Task 3). ✅
- **Placeholders:** all code complete. ✅
- **Type consistency:** `_momentum_leg` signature, `Side` annotation, reuse of `_favorite_ladder`
  and the $-value commit threshold; `_favorite_leg`/`_certainty`/`_pick_favorite_side` removed and
  not referenced. ✅
- **Scope:** paper only; favorite knobs left deprecated (not removed). ✅

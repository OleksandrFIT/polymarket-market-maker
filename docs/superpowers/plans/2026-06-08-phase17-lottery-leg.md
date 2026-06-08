# Phase-17 Cheap-Tail Lottery Leg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Add a small cheap-tail lottery leg on the underdog (competitor parity) alongside the
unchanged favorite engine. Paper only; no live trading.

**Architecture:** Refactor `compute_ladder` into `_favorite_leg` (existing phase-16 logic, with
one commit-one-side adjustment) + new `_lottery_leg`; `compute_ladder` returns their sum. Four
new config knobs, added to the phase-A live-settings whitelist and dashboard form.

**Tech:** Python 3.13, pytest. Test: `.venv/bin/pytest <path> -v`. Branch: master.
**Spec:** `docs/superpowers/specs/2026-06-08-phase17-lottery-leg-design.md`

---

## Task 1: Config knobs + phase-A whitelist + dashboard list

**Files:** Modify `quoter/config.py`; Modify `quoter/ops/live_settings.py`; Modify
`quoter/ops/dashboard.py`; Modify `tests/test_config.py`; Modify `tests/test_live_settings.py`.

- [ ] **Step 1: Update `tests/test_config.py`** — add to the `test_phase16_defaults` method
  (rename it `test_phase17_defaults`) these asserts:
```python
        assert c.lottery_max_price == 0.40
        assert c.lottery_cap_usd == 3.0
        assert c.lottery_size == 5
        assert c.lottery_levels == 2
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_config.py -v` → FAIL (no lottery_max_price).

- [ ] **Step 3: Add knobs to `quoter/config.py`** in the Phase-15/16 strategy block, after
  `min_time_to_expiry_sec`:
```python
    # ── Phase-17 cheap-tail lottery leg (competitor parity) ──
    lottery_max_price: float = 0.40    # buy underdog only if its price <= this
    lottery_cap_usd: float = 3.0       # separate small $ budget for the lottery leg
    lottery_size: int = 5              # shares per lottery bid (0 disables)
    lottery_levels: int = 2            # cheap-tail lottery bids per tick (0 disables)
```

- [ ] **Step 4: Add the four knobs to the phase-A whitelist** in
  `quoter/ops/live_settings.py` `_SPEC` dict:
```python
    "lottery_max_price": (float, 0.10, 0.49),
    "lottery_cap_usd": (float, 0.0, 50.0),
    "lottery_size": (int, 0, 50),
    "lottery_levels": (int, 0, 5),
```

- [ ] **Step 5: Add the four keys to the dashboard form** in `quoter/ops/dashboard.py` — append
  to the `SETTING_KEYS` JS array:
```javascript
  "lottery_max_price","lottery_cap_usd","lottery_size","lottery_levels"
```

- [ ] **Step 6: Update `tests/test_live_settings.py`** — `test_effective_has_all_keys` already
  uses `ALLOWED_KEYS` dynamically, so it still passes. Add one test confirming a lottery knob
  validates:
```python
def test_lottery_knob_validates(tmp_path):
    ls = _ls(tmp_path)
    ls.update("lottery_max_price", 0.30)
    assert ls.snapshot()["lottery_max_price"] == 0.30
    import pytest as _p
    with _p.raises(ValueError):
        ls.update("lottery_max_price", 0.9)  # > 0.49 range
```

- [ ] **Step 7: Run** `.venv/bin/pytest tests/test_config.py tests/test_live_settings.py -v` →
  PASS. (test_ladder may be unaffected here; full suite deferred to Task 2/3.)

- [ ] **Step 8: Commit.**
```bash
git add quoter/config.py quoter/ops/live_settings.py quoter/ops/dashboard.py tests/test_config.py tests/test_live_settings.py
git commit -m "feat(phase-17): lottery config knobs + phase-A whitelist + dashboard fields"
```

---

## Task 2: compute_ladder — `_favorite_leg` + `_lottery_leg`

**Files:** Modify `quoter/strategy/ladder.py`; Modify `tests/test_ladder.py`.

- [ ] **Step 1: Rewrite/extend `tests/test_ladder.py`.** Keep the existing favorite tests as-is
  (they still hold — favorite leg unchanged when lottery present at default mids). REMOVE
  `test_only_favorite_side` (no longer true). Add:
```python
def test_lottery_adds_underdog_bids():
    # mid 0.90 → favorite YES + lottery NO (underdog price 0.10 <= 0.40)
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert any(q.side == "YES" for q in out)
    assert any(q.side == "NO" for q in out)


def test_lottery_price_band():
    # mid 0.55 → underdog price 0.45 > lottery_max_price 0.40 → no NO lottery
    out = compute_ladder(Config(), mid_yes=0.55, time_to_expiry=LATE_TTE)
    assert all(q.side != "NO" for q in out)


def test_lottery_size_zero_disables():
    cfg = replace(Config(), lottery_size=0)
    out = compute_ladder(cfg, mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert out and all(q.side == "YES" for q in out)


def test_lottery_cap_stops():
    # large underdog inventory → lottery cap fires → no NO lottery bids
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_no_qty=1000)
    assert all(q.side != "NO" for q in out)


def test_lottery_exempt_from_commit():
    # holding favorite YES still allows NO lottery (commit gate uses majority holding)
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=50)
    assert any(q.side == "YES" for q in out)   # favorite still quotes (majority YES)
    assert any(q.side == "NO" for q in out)    # lottery NO present


def test_lottery_exempt_from_entry_start():
    # early window: favorite leg blocked by entry_start_frac, lottery still bids
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=290.0)
    assert out and all(q.side == "NO" for q in out)  # only lottery NO (favorite gated out)


def test_lottery_flip_does_not_unseat_favorite():
    # hold YES 50 (favorite) + NO 30 (lottery); favorite stays YES (majority), not flipped
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=50, inventory_no_qty=30)
    assert any(q.side == "YES" for q in out)
```
  Ensure `from dataclasses import replace` is imported in the test file.

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_ladder.py -v` → FAIL (lottery not implemented).

- [ ] **Step 3: Refactor `quoter/strategy/ladder.py`.** Replace the current `compute_ladder`
  function with the following three functions (the favorite logic moves verbatim into
  `_favorite_leg`, with ONLY the commit-one-side comparison changed to majority-holding):

```python
def _favorite_leg(
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    prev_mid_yes: float | None,
    inventory_yes_qty: int,
    inventory_no_qty: int,
    timeframe: str,
    window_length_sec: float | None,
    velocity_short: float | None,
) -> list[Quote]:
    """Phase-16 favorite engine (unchanged except commit uses majority holding)."""
    if window_length_sec is None:
        window_length_sec = 900.0 if timeframe == "15m" else 300.0
    time_into_window = max(0.0, window_length_sec - time_to_expiry)
    window_frac = min(1.0, time_into_window / window_length_sec)
    if window_frac < cfg.entry_start_frac:
        return []

    side = _pick_favorite_side(mid_yes, velocity_short, cfg)
    if side is None:
        return []

    # Commit-to-one-side: stick to the side we hold MORE of (favorite >> lottery shares).
    # Identical to phase-16 when the lottery is off (only one side ever held).
    if inventory_yes_qty > inventory_no_qty and side == "NO":
        return []
    if inventory_no_qty > inventory_yes_qty and side == "YES":
        return []

    fav_price = mid_yes if side == "YES" else (1.0 - mid_yes)
    if fav_price < cfg.favorite_min_price:
        return []

    if prev_mid_yes is not None:
        prev_fav = prev_mid_yes if side == "YES" else (1.0 - prev_mid_yes)
        if fav_price < prev_fav - cfg.rise_tolerance_cents:
            return []

    c = _certainty(fav_price, window_frac, cfg)
    cap_usd = cfg.per_market_cap_usd * (1.0 + c * (cfg.certainty_cap_multiplier - 1.0))
    fav_qty = inventory_yes_qty if side == "YES" else inventory_no_qty
    if fav_qty * fav_price >= cap_usd:
        return []

    return _favorite_ladder(side, fav_price, cfg.flat_size, cfg)


def _lottery_leg(
    cfg: Config, mid_yes: float, inventory_yes_qty: int, inventory_no_qty: int,
) -> list[Quote]:
    """Small cheap-tail lottery bids on the underdog side (competitor parity).

    Exempt from the favorite-leg gates (commit-one-side, entry_start_frac,
    velocity, favorite_min_price). Bounded by its own small lottery_cap_usd.
    """
    if cfg.lottery_size <= 0 or cfg.lottery_levels <= 0:
        return []
    if mid_yes >= 0.5:
        side, price = "NO", round(1.0 - mid_yes, 2)
    else:
        side, price = "YES", round(mid_yes, 2)
    if price <= 0.0 or price > cfg.lottery_max_price:
        return []
    udog_qty = inventory_yes_qty if side == "YES" else inventory_no_qty
    if udog_qty * price >= cfg.lottery_cap_usd:
        return []
    out: list[Quote] = []
    for i in range(cfg.lottery_levels):
        p = round(price - i * 0.01, 2)
        if p <= 0.0:
            continue
        out.append(Quote(side, p, cfg.lottery_size))
    return out


def compute_ladder(
    cfg: Config,
    mid_yes: float,
    time_to_expiry: float,
    *,
    prev_mid_yes: float | None = None,
    inventory_yes_qty: int = 0,
    inventory_no_qty: int = 0,
    timeframe: str = "5m",
    asset: str | None = None,
    window_length_sec: float | None = None,
    velocity_short: float | None = None,
    # committed_side and velocity_long: legacy kwargs accepted for caller compat; ignored.
    committed_side: Side | None = None,
    velocity_long: float | None = None,
) -> list[Quote]:
    """Favorite engine (one side, committed) + cheap-tail lottery on the underdog."""
    if not (0.02 <= mid_yes <= 0.99) or time_to_expiry < cfg.min_time_to_expiry_sec:
        return []
    favorite = _favorite_leg(
        cfg, mid_yes, time_to_expiry, prev_mid_yes,
        inventory_yes_qty, inventory_no_qty, timeframe, window_length_sec, velocity_short,
    )
    lottery = _lottery_leg(cfg, mid_yes, inventory_yes_qty, inventory_no_qty)
    return favorite + lottery
```
  Keep `_pick_favorite_side`, `_certainty`, `_favorite_ladder`, `Quote`, `Side` as they are.
  Update the module docstring to mention the lottery leg.

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_ladder.py tests/test_config.py -v` → PASS.
  (test_backtest_engine may shift; fixed/confirmed in Task 3.)

- [ ] **Step 5: Commit.**
```bash
git add quoter/strategy/ladder.py tests/test_ladder.py
git commit -m "feat(phase-17): add cheap-tail lottery leg alongside favorite engine"
```

---

## Task 3: Backtest comparison + report

**Files:** Modify `quoter/backtest/run_backtest.py`; (verify `tests/test_backtest_engine.py`).

- [ ] **Step 1: Replace the `main` function in `quoter/backtest/run_backtest.py`** to compare
  phase-17 (lottery on) vs lottery-off:
```python
def main() -> None:
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}
    p17 = run_config(Config(), markets, series_by_market, "phase-17(lottery on)")
    p16 = run_config(replace(Config(), lottery_size=0), markets, series_by_market,
                     "phase-16(lottery off)")
    _print_table([p17, p16])
    print(f"\nRecorded live baseline (state.db): {RECORDED_LIVE_BASELINE:.2f}")
    print(f"Lottery delta (p17 - p16): {p17.total_pnl - p16.total_pnl:+.2f}")
```
  Leave `sweep()` as-is.

- [ ] **Step 2: Run the backtest** `.venv/bin/python -m quoter.backtest.run_backtest` →
  records phase-17 vs lottery-off + the lottery delta (no traceback). Note the numbers.

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_backtest_engine.py tests/test_backtest_runner.py -v`
  → PASS. If `test_backtest_runner` asserts old `main` row labels, update to the new labels.

- [ ] **Step 4: Run the FULL suite** `.venv/bin/pytest -q` → ALL green.

- [ ] **Step 5: Write `reports/2026-06-08_phase17-lottery.md`** with: the phase-17 vs
  lottery-off numbers, the lottery delta, and the honest note (the lottery is ~3-4% of money —
  expected near-neutral; it achieves profile identity, not a PnL jump; fill-model caveats from
  prior reports still apply).

- [ ] **Step 6: Commit.**
```bash
git add quoter/backtest/run_backtest.py tests/test_backtest_runner.py reports/2026-06-08_phase17-lottery.md
git commit -m "feat(phase-17): backtest lottery-on vs lottery-off comparison + report"
```

---

## Self-Review
- **Spec coverage:** lottery leg (Task 2), config + whitelist + dashboard (Task 1), backtest
  comparison + report (Task 3), commit-one-side majority adjustment (Task 2). ✅
- **Placeholders:** all code complete. ✅
- **Type consistency:** `_favorite_leg`/`_lottery_leg`/`compute_ladder` signatures consistent;
  4 lottery knobs named identically across config/whitelist/dashboard/tests. ✅
- **Scope:** paper only; favorite engine logic preserved (identical when lottery off). ✅

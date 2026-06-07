# Phase-16 Full-Replication Tactic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Match the competitor's profitable profile — late (last 40%), high-price (≥0.85)
favorite-buying, one side only, flat size. Paper only; do NOT touch live trading.

**Architecture:** All changes are config-value edits plus two small edits inside the existing
`compute_ladder`: commit-to-one-side (using the inventory args it already receives) and flat
per-tick size (replacing certainty-scaled size). The per-market cap is unchanged.

**Tech Stack:** Python 3.13, frozen `dataclass` Config, pure `compute_ladder`, pytest.
Test command: `.venv/bin/pytest <path> -v`. Branch: `master`.

**Spec:** `docs/superpowers/specs/2026-06-07-phase16-full-replication-design.md`

---

## Task 1: Config values + flat_size knob

**Files:** Modify `quoter/config.py`; Modify `tests/test_config.py`.

- [ ] **Step 1: Update the config test.** In `tests/test_config.py`, replace the
  `test_phase15_defaults` method with:

```python
    def test_phase16_defaults(self):
        c = Config()
        assert c.favorite_min_price == 0.85
        assert c.max_entry_price == 0.97
        assert c.entry_start_frac == 0.60
        assert c.flat_size == 10
        assert c.per_market_cap_usd == 50.0
        assert c.certainty_cap_multiplier == 2.0
        assert c.velocity_confirm_threshold == 0.0005
        assert c.rise_tolerance_cents == 0.01
        assert c.favorite_ladder_levels == 3
        assert c.min_time_to_expiry_sec == 5.0
```

- [ ] **Step 2: Run it, expect fail.** `.venv/bin/pytest tests/test_config.py::TestConfig::test_phase16_defaults -v` → FAIL (no `flat_size`, wrong defaults).

- [ ] **Step 3: Edit `quoter/config.py`.** In the Phase-15 block, change three defaults and
  swap the two certainty-size knobs for `flat_size`. The current lines are:

```python
    favorite_min_price: float = 0.55       # below this no clear favorite → no quotes
    max_entry_price: float = 0.95          # hard ceiling on any bid (backtest-swept)
    entry_start_frac: float = 0.30         # no entries before this fraction of window
    certainty_size_base: int = 5           # base shares per tick (Polymarket min)
    certainty_size_max: int = 40           # shares per tick at max certainty
```

Replace them with:

```python
    favorite_min_price: float = 0.85       # phase-16: only near-certain favorites
    max_entry_price: float = 0.97          # phase-16: allow >=0.95 like competitor
    entry_start_frac: float = 0.60         # phase-16: only the last 40% of window
    flat_size: int = 10                    # phase-16: flat shares per tick (no ramp-into-price)
```

Leave all other knobs (`per_market_cap_usd`, `certainty_cap_multiplier`,
`velocity_confirm_threshold`, `rise_tolerance_cents`, `favorite_ladder_levels`,
`min_time_to_expiry_sec`) unchanged.

- [ ] **Step 4: Run config tests, expect pass.** `.venv/bin/pytest tests/test_config.py -v` → PASS.

  > NOTE: `tests/test_ladder.py` will be RED after this step (it imports `_certainty_size` and
  > uses old thresholds). That is fixed in Task 2. Do not run the full suite yet.

- [ ] **Step 5: Commit.**
```bash
git add quoter/config.py tests/test_config.py
git commit -m "feat(phase-16): config defaults for late+high entry + flat_size"
```

---

## Task 2: compute_ladder — commit-to-one-side + flat size

**Files:** Modify `quoter/strategy/ladder.py`; Rewrite `tests/test_ladder.py`.

- [ ] **Step 1: Rewrite `tests/test_ladder.py`** with (note: mids ≥0.85, tte=60 → 5m
  window_frac=(300-60)/300=0.80 ≥ 0.60):

```python
"""Tests for phase-16 full-replication compute_ladder (late + high + one-sided + flat)."""

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder

LATE_TTE = 60.0  # 5m: window_frac = (300-60)/300 = 0.80 (>= entry_start_frac 0.60)


def test_picks_higher_side_as_favorite():
    yes = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert yes and all(q.side == "YES" for q in yes)
    no = compute_ladder(Config(), mid_yes=0.10, time_to_expiry=LATE_TTE)
    assert no and all(q.side == "NO" for q in no)


def test_below_min_price_no_quotes():
    # favorite 0.80 < favorite_min_price 0.85
    assert compute_ladder(Config(), mid_yes=0.80, time_to_expiry=LATE_TTE) == []


def test_too_early_no_quotes():
    # tte=200 → window_frac=(300-200)/300=0.33 < entry_start_frac 0.60
    assert compute_ladder(Config(), mid_yes=0.90, time_to_expiry=200.0) == []


def test_caps_at_max_entry_price():
    q = compute_ladder(Config(), mid_yes=0.985, time_to_expiry=LATE_TTE)
    assert q and max(x.price for x in q) <= Config().max_entry_price


def test_commit_one_side_holds_yes():
    # we already hold YES → a NO-favorite mid must NOT add NO
    out = compute_ladder(Config(), mid_yes=0.10, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=10)
    assert out == []


def test_commit_one_side_holds_no():
    # we already hold NO → a YES-favorite mid must NOT add YES
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_no_qty=10)
    assert out == []


def test_commit_one_side_same_side_ok():
    # holding YES and YES still favorite → still quotes YES
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=10)
    assert out and all(q.side == "YES" for q in out)


def test_flat_size():
    cfg = Config()
    out = compute_ladder(cfg, mid_yes=0.90, time_to_expiry=LATE_TTE)
    assert out and all(q.size == cfg.flat_size for q in out)


def test_falling_favorite_suppressed():
    # YES favorite price fell 0.95 → 0.90 (drop 0.05 > rise_tolerance 0.01)
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, prev_mid_yes=0.95)
    assert out == []


def test_velocity_disagree_blocks():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=-0.01)
    assert out == []


def test_velocity_none_falls_back():
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE, velocity_short=None)
    assert out


def test_only_favorite_side():
    out = compute_ladder(Config(), mid_yes=0.92, time_to_expiry=LATE_TTE)
    assert out and len({q.side for q in out}) == 1


def test_per_market_cap_stops_adds():
    # large existing favorite inventory → spent proxy exceeds cap → []
    out = compute_ladder(Config(), mid_yes=0.90, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=1000)
    assert out == []
```

- [ ] **Step 2: Run, expect fail.** `.venv/bin/pytest tests/test_ladder.py -v` → FAIL
  (commit-one-side not implemented; `_certainty_size` import removed but flat size not used yet).

- [ ] **Step 3: Edit `quoter/strategy/ladder.py`.**

  (a) DELETE the `_certainty_size` function entirely (the one that returns
  `cfg.certainty_size_base + int(round(c * span))`). Keep `_certainty` (the cap uses it).

  (b) In `compute_ladder`, immediately AFTER the favorite-side selection block:
```python
    side = _pick_favorite_side(mid_yes, velocity_short, cfg)
    if side is None:
        return []
```
  add the commit-to-one-side gate:
```python
    # Phase-16 commit-to-one-side: once we hold a side this window, only quote it.
    if inventory_yes_qty > 0 and side == "NO":
        return []
    if inventory_no_qty > 0 and side == "YES":
        return []
```

  (c) In `compute_ladder`, replace the sizing line:
```python
    size = _certainty_size(fav_price, window_frac, cfg)
```
  with:
```python
    size = cfg.flat_size
```
  Leave the per-market cap block (`_certainty`, `cap_usd`, `fav_qty * fav_price >= cap_usd`)
  unchanged.

- [ ] **Step 4: Run ladder + config tests, expect pass.**
  `.venv/bin/pytest tests/test_ladder.py tests/test_config.py -v` → PASS.
  > NOTE: `tests/test_backtest_engine.py` may be RED (its scenario predates the new gates);
  > fixed in Task 3. Don't run the full suite yet.

- [ ] **Step 5: Commit.**
```bash
git add quoter/strategy/ladder.py tests/test_ladder.py
git commit -m "feat(phase-16): commit-to-one-side + flat size in compute_ladder"
```

---

## Task 3: Backtest scenario + favorite_min_price × entry_start_frac sweep

**Files:** Modify `tests/test_backtest_engine.py`; Modify `quoter/backtest/run_backtest.py`.

- [ ] **Step 1: Update the engine test scenario.** In `tests/test_backtest_engine.py`,
  replace `test_buying_winning_favorite_is_profitable` so the rising favorite is in the ≥0.85
  band during the last 40% of the window (so phase-16 gates pass). Use:

```python
def test_buying_winning_favorite_is_profitable():
    cfg = Config()
    # Late, rising YES favorite in the >=0.85 band during the last 40% of a 5m
    # window (open=0, expire=300): times 200,240,280,295 → window_frac 0.67..0.98.
    series = [PricePoint(200, 0.86), PricePoint(240, 0.90),
              PricePoint(280, 0.95), PricePoint(295, 0.99)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty > 0
    assert res.pnl > 0
```

  Leave `test_no_favorite_no_fills_no_pnl` as-is (flat 0.50 → still below favorite_min_price →
  no quotes), but if its `series` times put window_frac < 0.60 it already returns `[]`; verify
  it still asserts `yes_qty == 0`, `total_cost == 0.0`, `pnl == 0.0`.

- [ ] **Step 2: Run engine test, expect pass after Step 3 is not needed for it; run now.**
  `.venv/bin/pytest tests/test_backtest_engine.py -v` → PASS (the engine already calls the real
  compute_ladder; the new scenario exercises phase-16 defaults).

- [ ] **Step 3: Extend the sweep in `quoter/backtest/run_backtest.py`.** Replace the `sweep`
  function with a grid over `favorite_min_price` × `entry_start_frac` (keeping a column for
  total/worst):

```python
def sweep() -> None:
    """Grid over favorite_min_price x entry_start_frac (phase-16 profitable-region check)."""
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}
    summaries: list[ConfigSummary] = []
    for fmin in (0.80, 0.85, 0.90):
        for estart in (0.50, 0.60, 0.70):
            cfg = replace(Config(), favorite_min_price=fmin, entry_start_frac=estart)
            summaries.append(
                run_config(cfg, markets, series_by_market,
                           f"fmin={fmin} estart={estart}")
            )
    summaries.sort(key=lambda s: s.total_pnl, reverse=True)
    _print_table(summaries)
```

  Leave `main` as-is (it runs `Config()` phase-16 defaults vs the recorded baseline).

- [ ] **Step 4: Run the backtest module (no traceback).**
  `.venv/bin/python -m quoter.backtest.run_backtest` → one-row phase-16 table + baseline delta.
  `.venv/bin/python -m quoter.backtest.run_backtest --sweep` → 9 rows sorted by total PnL.
  Record the printed numbers in the report (Step 6).

- [ ] **Step 5: Run backtest tests + FULL suite, expect green.**
  `.venv/bin/pytest tests/test_backtest_engine.py tests/test_backtest_runner.py -v` → PASS.
  Then `.venv/bin/pytest -q` → ALL green. If `test_backtest_runner.py` asserts old row names,
  update them to the new `fmin=… estart=…` labels.

- [ ] **Step 6: Write the report.** Create `reports/2026-06-07_phase16-backtest.md` with: the
  phase-16-vs-baseline numbers, the 9-cell sweep grid, worst-case comparison vs phase-15, and
  the honest caveats (coarse fill model; cap not modelled in backtest; small sample; backtest
  is a filter, paper is the real test).

- [ ] **Step 7: Commit.**
```bash
git add tests/test_backtest_engine.py quoter/backtest/run_backtest.py tests/test_backtest_runner.py reports/2026-06-07_phase16-backtest.md
git commit -m "feat(phase-16): backtest scenario + fmin×estart sweep + report"
```

---

## Self-Review

**Spec coverage:** late+high entry (Task 1 config), commit-one-side (Task 2), flat size
(Task 1+2), cap unchanged (untouched), backtest+sweep+report (Task 3). ✅
**Placeholders:** none — all steps have concrete code. ✅
**Type consistency:** `flat_size` int used as `Quote.size`; `compute_ladder` signature
unchanged (commit-one-side uses existing `inventory_*` args); `_certainty` kept for cap,
`_certainty_size` removed and no longer referenced. ✅
**Scope:** paper only; no live-trading code touched. ✅

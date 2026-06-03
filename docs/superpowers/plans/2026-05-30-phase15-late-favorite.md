# Phase-15 Late-Window Favorite-Buying Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the symmetric two-sided ladder with a one-sided late-window
favorite-buying strategy that follows the Polymarket price (buy the side already pricing as
the winner, scale size with certainty, never add to a falling side, hold to resolution).

**Architecture:** `compute_ladder` is rewritten in place as a pure function that emits
one-sided favorite bids. New phase-15 config knobs are added; dead phase-11/12/13/14 knobs
are removed last (once nothing references them). The buy-on-rise gate needs the previous
mid, threaded in as an optional `prev_mid_yes` parameter from `quoter_loop` (live/paper) and
from the prior series point (backtest). The backtest compares the new strategy to the
recorded live baseline (−$282.53) and sweeps `max_entry_price`.

**Tech Stack:** Python 3.13, frozen `dataclass` Config, pure `compute_ladder`, pytest
(`asyncio_mode=auto`). Test command: `.venv/bin/pytest <path> -v`.

**Spec:** `docs/superpowers/specs/2026-05-30-phase15-late-favorite-design.md`

**Branch:** Work directly on `master` (user's standing choice).

> **Planning refinements vs spec (intentional, flagged to user at handoff):**
> 1. `dead_zone_half_width` is dropped — it was redundant with `favorite_min_price`
>    (favorite price < 0.55 ⟺ mid in (0.45, 0.55), the coin-flip zone). A single
>    `favorite_min_price` gate covers both the dead-zone and the min-certainty floor.
> 2. The favorite ladder's top bid sits **at** `fav_price` (not `fav_price − 0.01`), so the
>    bids actually fill in a rising market (the fill model only fills a BUY when the side's
>    price dips to/below the bid; a bid 1c below a rising favorite would never fill). This
>    matches the competitor, who aggressively buys at the favorite price.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `quoter/config.py` | runtime knobs | add phase-15 block; later remove dead knobs |
| `quoter/strategy/ladder.py` | strategy: `compute_ladder` + helpers | full rewrite |
| `tests/test_ladder.py` | strategy unit tests | full rewrite |
| `tests/test_config.py` | config unit tests | update default assertions |
| `quoter/backtest/engine.py` | replay one window | thread `prev_mid_yes` |
| `quoter/backtest/run_backtest.py` | A/B + sweep runner | rewrite `main`/`sweep` |
| `tests/test_backtest_engine.py` | engine tests | rewrite scenarios |
| `quoter/quoter_loop.py` | live/paper requote loop | thread `prev_mid_yes` |

---

## Task 1: Phase-15 config knobs

**Files:**
- Modify: `quoter/config.py` (Phase-14 block ~lines 60-65)
- Test: `tests/test_config.py`

Add the phase-15 knobs and raise `max_entry_price` to 0.95. Keep the old knobs for now
(removed in Task 5 once unreferenced).

- [ ] **Step 1: Update the failing test**

In `tests/test_config.py`, replace the `test_new_tactic_defaults` method with:

```python
    def test_phase15_defaults(self):
        c = Config()
        assert c.favorite_min_price == 0.55
        assert c.max_entry_price == 0.95
        assert c.entry_start_frac == 0.30
        assert c.certainty_size_base == 5
        assert c.certainty_size_max == 40
        assert c.per_market_cap_usd == 50.0
        assert c.certainty_cap_multiplier == 2.0
        assert c.velocity_confirm_threshold == 0.0005
        assert c.rise_tolerance_cents == 0.01
        assert c.favorite_ladder_levels == 3
        assert c.min_time_to_expiry_sec == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py::TestConfig::test_phase15_defaults -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'favorite_min_price'`

- [ ] **Step 3: Add the phase-15 block to `quoter/config.py`**

Find this block (the Phase-14 section):

```python
    # ── Phase-14 entry discipline (Bonereaper-style early-entry + price-cap) ──
    # Data (2026-05-28): -$208 of -$247 loss came from fills after 66% of the
    # window; -$162 from fills above price 0.60 (chasing the favorite into
    # whipsaw). Stop adding inventory late, and never bid above max_entry_price.
    entry_cutoff_frac: float = 0.50   # no new quotes after this fraction of window
    max_entry_price: float = 0.60     # drop any quote priced above this
```

Replace it with:

```python
    # ── Phase-15 late-window favorite-buying ──
    # Strategy follows the Polymarket price: late in the window the mid has
    # converged toward the outcome, so we BUY the favorite (the side priced
    # > 0.5), one side only, scaling size with certainty, never adding to a
    # falling side. Buy-only, held to resolution.
    favorite_min_price: float = 0.55       # below this no clear favorite → no quotes
    max_entry_price: float = 0.95          # hard ceiling on any bid (backtest-swept)
    entry_start_frac: float = 0.30         # no entries before this fraction of window
    certainty_size_base: int = 5           # base shares per tick (Polymarket min)
    certainty_size_max: int = 40           # shares per tick at max certainty
    per_market_cap_usd: float = 50.0       # base $ ceiling per market
    certainty_cap_multiplier: float = 2.0  # cap scales up to ×this under certainty
    velocity_confirm_threshold: float = 0.0005  # min Binance velocity to confirm side
    rise_tolerance_cents: float = 0.01     # favorite may dip this much vs prev and still quote
    favorite_ladder_levels: int = 3        # one-sided bids per tick
    min_time_to_expiry_sec: float = 5.0    # below this → no quotes

    # Legacy phase-14 knob, unused by phase-15; removed in cleanup task.
    entry_cutoff_frac: float = 0.50
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (all config tests)

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_config.py
git commit -m "feat(phase-15): add late-favorite config knobs"
```

---

## Task 2: Rewrite `compute_ladder` to one-sided favorite logic

**Files:**
- Rewrite: `quoter/strategy/ladder.py`
- Rewrite: `tests/test_ladder.py`

This replaces the entire strategy. The new `compute_ladder` keeps a backward-compatible
signature (legacy kwargs accepted and ignored) plus a new optional `prev_mid_yes`.

- [ ] **Step 1: Write the new test file**

Overwrite `tests/test_ladder.py` with:

```python
"""Tests for phase-15 one-sided late-window favorite-buying compute_ladder."""

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder, _certainty_size

# 5m window: window_frac = (300 - tte) / 300. tte=120 → frac 0.60 (late enough).
LATE_TTE = 120.0


def test_picks_higher_side_as_favorite():
    yes = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE)
    assert yes and all(q.side == "YES" for q in yes)
    no = compute_ladder(Config(), mid_yes=0.30, time_to_expiry=LATE_TTE)
    assert no and all(q.side == "NO" for q in no)


def test_dead_zone_no_quotes():
    assert compute_ladder(Config(), mid_yes=0.50, time_to_expiry=LATE_TTE) == []
    assert compute_ladder(Config(), mid_yes=0.52, time_to_expiry=LATE_TTE) == []


def test_too_early_no_quotes():
    # tte=290 → window_frac = (300-290)/300 = 0.033 < entry_start_frac 0.30
    assert compute_ladder(Config(), mid_yes=0.70, time_to_expiry=290.0) == []


def test_below_min_price_no_quotes():
    # favorite price 0.53 < favorite_min_price 0.55
    assert compute_ladder(Config(), mid_yes=0.53, time_to_expiry=LATE_TTE) == []


def test_caps_at_max_entry_price():
    q = compute_ladder(Config(), mid_yes=0.96, time_to_expiry=LATE_TTE)
    assert q and max(x.price for x in q) <= Config().max_entry_price


def test_falling_favorite_suppressed():
    # YES favorite price fell 0.75 → 0.70 (drop 0.05 > rise_tolerance 0.01)
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, prev_mid_yes=0.75)
    assert out == []


def test_rising_favorite_allowed():
    # YES favorite price rose 0.65 → 0.70 → quotes
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, prev_mid_yes=0.65)
    assert out


def test_velocity_disagree_blocks():
    # YES favorite but BTC velocity negative (down) → blocked
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, velocity_short=-0.01)
    assert out == []


def test_velocity_agree_allowed():
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, velocity_short=0.01)
    assert out


def test_velocity_none_falls_back():
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE, velocity_short=None)
    assert out  # not blocked when velocity unavailable (backtest)


def test_certainty_size_monotonic():
    cfg = Config()
    low = _certainty_size(0.60, 0.40, cfg)
    high = _certainty_size(0.90, 0.95, cfg)
    assert high > low


def test_per_market_cap_stops_adds():
    # large existing favorite inventory → spent proxy exceeds cap → []
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE,
                         inventory_yes_qty=1000)
    assert out == []


def test_only_favorite_side():
    out = compute_ladder(Config(), mid_yes=0.80, time_to_expiry=LATE_TTE)
    assert out and len({q.side for q in out}) == 1


def test_legacy_kwargs_accepted():
    # quoter_loop still passes committed_side / velocity_long; must not error.
    out = compute_ladder(Config(), mid_yes=0.70, time_to_expiry=LATE_TTE,
                         committed_side="YES", velocity_long=0.0, timeframe="5m",
                         asset="BTC")
    assert out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ladder.py -v`
Expected: FAIL (old `compute_ladder` produces two-sided ladders; many asserts fail / import of `_certainty_size` fails)

- [ ] **Step 3: Rewrite `quoter/strategy/ladder.py`**

Overwrite the entire file with:

```python
"""Phase-15 strategy: one-sided late-window favorite-buying.

We follow the Polymarket price. Late in the window the mid has converged toward
the actual outcome, so we BUY the side already pricing as the winner (the
favorite), one side only, scaling size as certainty rises, and never adding to a
falling side (anti-knife). Buy-only; positions are held to resolution (no sells).

Pure function: compute_ladder(cfg, mid_yes, time_to_expiry, ...) -> list[Quote].
All state (the previous mid, for the buy-on-rise gate) is passed in by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from quoter.config import Config

Side = Literal["YES", "NO"]


@dataclass
class Quote:
    """A single resting BUY bid on one outcome token."""

    side: Side
    price: float  # 0.01 .. 0.99 (cents)
    size: int     # shares; Polymarket min 5


def _pick_favorite_side(
    mid_yes: float, velocity_short: float | None, cfg: Config,
) -> Side | None:
    """Favorite = the side priced > 0.5. Require Binance velocity to agree if given.

    velocity_short None (e.g. backtest) → skip the confirmation (mid-only).
    """
    side: Side = "YES" if mid_yes > 0.5 else "NO"
    if velocity_short is not None:
        thr = cfg.velocity_confirm_threshold
        if side == "YES" and velocity_short < thr:
            return None
        if side == "NO" and velocity_short > -thr:
            return None
    return side


def _certainty(price: float, window_frac: float, cfg: Config) -> float:
    """Score in [0, 1] rising with BOTH favorite price and window progress.

    Product form: certainty is high only when the price is firm AND the window
    is late — mirroring the competitor's dollar curve (small early, big late).
    """
    pc = (price - cfg.favorite_min_price) / max(
        cfg.max_entry_price - cfg.favorite_min_price, 1e-9,
    )
    tc = (window_frac - cfg.entry_start_frac) / max(1.0 - cfg.entry_start_frac, 1e-9)
    pc = min(1.0, max(0.0, pc))
    tc = min(1.0, max(0.0, tc))
    return pc * tc


def _certainty_size(price: float, window_frac: float, cfg: Config) -> int:
    """Per-tick share size, scaling base..max with certainty."""
    c = _certainty(price, window_frac, cfg)
    span = cfg.certainty_size_max - cfg.certainty_size_base
    return cfg.certainty_size_base + int(round(c * span))


def _favorite_ladder(
    side: Side, fav_price: float, size: int, cfg: Config,
) -> list[Quote]:
    """`favorite_ladder_levels` bids descending by 1c from fav_price.

    Top bid sits AT the favorite price so it actually fills as the favorite
    firms; lower bids catch small dips. All capped at max_entry_price.
    """
    top = min(round(fav_price, 2), cfg.max_entry_price)
    out: list[Quote] = []
    for i in range(cfg.favorite_ladder_levels):
        p = round(top - i * 0.01, 2)
        if p <= 0.0 or p > cfg.max_entry_price:
            continue
        out.append(Quote(side, p, size))
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
    # Legacy kwargs accepted for caller compatibility; ignored in phase-15.
    committed_side: Side | None = None,
    velocity_long: float | None = None,
) -> list[Quote]:
    """Return one-sided favorite BUY bids. See module docstring for the rules."""
    if not (0.02 <= mid_yes <= 0.98) or time_to_expiry < cfg.min_time_to_expiry_sec:
        return []

    if window_length_sec is None:
        window_length_sec = 900.0 if timeframe == "15m" else 300.0
    time_into_window = max(0.0, window_length_sec - time_to_expiry)
    window_frac = min(1.0, time_into_window / window_length_sec)
    if window_frac < cfg.entry_start_frac:
        return []

    side = _pick_favorite_side(mid_yes, velocity_short, cfg)
    if side is None:
        return []
    fav_price = mid_yes if side == "YES" else (1.0 - mid_yes)
    if fav_price < cfg.favorite_min_price:
        return []

    # Buy-on-rise: never add to a FALLING favorite (anti-knife).
    if prev_mid_yes is not None:
        prev_fav = prev_mid_yes if side == "YES" else (1.0 - prev_mid_yes)
        if fav_price < prev_fav - cfg.rise_tolerance_cents:
            return []

    # Per-market cap (USD), scaled up under certainty. Spent is approximated by
    # favorite-side shares × current favorite price.
    c = _certainty(fav_price, window_frac, cfg)
    cap_usd = cfg.per_market_cap_usd * (1.0 + c * (cfg.certainty_cap_multiplier - 1.0))
    fav_qty = inventory_yes_qty if side == "YES" else inventory_no_qty
    if fav_qty * fav_price >= cap_usd:
        return []

    size = _certainty_size(fav_price, window_frac, cfg)
    return _favorite_ladder(side, fav_price, size, cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ladder.py tests/test_config.py -v`
Expected: PASS (all ladder + config tests)

> Note: `tests/test_backtest_engine.py` will be RED after this task (its scenarios assume
> the old strategy). That is expected and is fixed in Task 3. Do not run the full suite yet.

- [ ] **Step 5: Commit**

```bash
git add quoter/strategy/ladder.py tests/test_ladder.py
git commit -m "feat(phase-15): rewrite compute_ladder as one-sided late-favorite"
```

---

## Task 3: Backtest — thread `prev_mid_yes`, sweep `max_entry_price`, compare to live baseline

**Files:**
- Modify: `quoter/backtest/engine.py`
- Modify: `quoter/backtest/run_backtest.py`
- Rewrite: `tests/test_backtest_engine.py`

- [ ] **Step 1: Rewrite the engine tests**

Overwrite `tests/test_backtest_engine.py` with:

```python
from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.engine import run_market


def _win_market():
    # 5m window, YES wins. open=0, expire=300.
    return MarketWindow(market_id="0xwin", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")


def test_buying_winning_favorite_is_profitable():
    cfg = Config()
    # Late, rising YES favorite (0.70 → 0.96) that resolves YES. Our top bid
    # sits at the favorite price each interval, so YES fills cheap-of-1.0 and
    # the winning shares pay out 1.0 → positive PnL.
    series = [PricePoint(120, 0.70), PricePoint(180, 0.80),
              PricePoint(240, 0.90), PricePoint(290, 0.96)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty > 0
    assert res.pnl > 0


def test_no_favorite_no_fills_no_pnl():
    cfg = Config()
    # Flat coin-flip at 0.50 → favorite below favorite_min_price → no quotes.
    series = [PricePoint(120, 0.50), PricePoint(180, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0
    assert res.pnl == res.yes_qty * 1.0 - res.total_cost
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_backtest_engine.py -v`
Expected: FAIL — `run_market` does not yet pass `prev_mid_yes`, so the buy-on-rise gate is
never exercised (test still relies only on the new ladder, but the engine must forward the
prior mid for the gate to behave on real data). Confirm both tests' intent before fixing.

- [ ] **Step 3: Thread `prev_mid_yes` in `quoter/backtest/engine.py`**

Replace the body of `run_market` (the `for` loop) so it forwards the prior point's YES price:

```python
def run_market(
    cfg: Config, window: MarketWindow, series: list[PricePoint],
) -> BacktestResult:
    """Replay `series` for `window` under `cfg`; return PnL at resolution."""
    yes_qty = no_qty = 0.0
    total_cost = 0.0
    n_fills = 0
    prev_yes: float | None = None

    for i in range(len(series) - 1):
        now, nxt = series[i], series[i + 1]
        tte = window.expire_ts - now.t
        if tte <= 0:
            break
        desired = compute_ladder(
            cfg,
            mid_yes=now.yes_price,
            time_to_expiry=float(tte),
            prev_mid_yes=prev_yes,
            timeframe=window.timeframe,
            asset=window.asset,
            window_length_sec=float(window.window_length),
        )
        for side, price, size in simulate_interval_fills(
            desired, now.yes_price, nxt.yes_price,
        ):
            if side == "YES":
                yes_qty += size
            else:
                no_qty += size
            total_cost += price * size
            n_fills += 1
        prev_yes = now.yes_price

    win_shares = yes_qty if window.winning_side == "YES" else no_qty
    pnl = win_shares * 1.0 - total_cost
    return BacktestResult(
        market_id=window.market_id, pnl=pnl, yes_qty=yes_qty,
        no_qty=no_qty, total_cost=total_cost, n_fills=n_fills,
    )
```

- [ ] **Step 4: Run engine tests to verify they pass**

Run: `.venv/bin/pytest tests/test_backtest_engine.py -v`
Expected: PASS

- [ ] **Step 5: Rewrite `main` and `sweep` in `quoter/backtest/run_backtest.py`**

Replace the `sweep` and `main` functions (which reference the removed `entry_cutoff_frac`
and directional knobs) with:

```python
RECORDED_LIVE_BASELINE = -282.53  # from state.db: 37 resolved markets, 8W/29L


def sweep() -> None:
    """Grid over max_entry_price; print PnL per ceiling (price band chosen on data)."""
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}
    summaries: list[ConfigSummary] = []
    for cap in (0.75, 0.85, 0.92, 0.97):
        cfg = replace(Config(), max_entry_price=cap)
        summaries.append(
            run_config(cfg, markets, series_by_market, f"cap={cap}")
        )
    summaries.sort(key=lambda s: s.total_pnl, reverse=True)
    _print_table(summaries)


def main() -> None:
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}
    phase15 = run_config(Config(), markets, series_by_market, "phase-15(late-fav)")
    _print_table([phase15])
    print(f"\nRecorded live baseline (state.db): {RECORDED_LIVE_BASELINE:.2f}")
    delta = phase15.total_pnl - RECORDED_LIVE_BASELINE
    print(f"Delta vs live baseline: {delta:+.2f}")
```

- [ ] **Step 6: Run the backtest module to confirm it executes**

Run: `.venv/bin/python -m quoter.backtest.run_backtest`
Expected: prints a one-row table for `phase-15(late-fav)` plus the baseline delta (no
traceback). Then run the sweep:
Run: `.venv/bin/python -m quoter.backtest.run_backtest --sweep`
Expected: prints four rows (`cap=0.75 .. cap=0.97`) sorted by total PnL.

- [ ] **Step 7: Run the backtest test files**

Run: `.venv/bin/pytest tests/test_backtest_engine.py tests/test_backtest_runner.py -v`
Expected: PASS. If `tests/test_backtest_runner.py` asserts the old `main`/`sweep` row names
(`baseline(phase-13)` / `new(early+cap)` / `cut=… cap=…`), update those assertions to the
new names (`phase-15(late-fav)`, `cap=0.75`, etc.) so they pass.

- [ ] **Step 8: Commit**

```bash
git add quoter/backtest/engine.py quoter/backtest/run_backtest.py tests/test_backtest_engine.py tests/test_backtest_runner.py
git commit -m "feat(phase-15): backtest threads prev_mid, sweeps max_entry_price vs live baseline"
```

---

## Task 4: Thread `prev_mid_yes` through the live/paper loop

**Files:**
- Modify: `quoter/quoter_loop.py`
- Test: `tests/test_quoter_loop.py`

The loop must remember each market's previous mid and pass it into `compute_ladder` so the
buy-on-rise gate works live and in paper.

- [ ] **Step 1: Add prev-mid storage in `__init__`**

In `quoter/quoter_loop.py`, in `QuoterLoop.__init__`, after the line
`self._last_requote_ts: dict[str, float] = {}` add:

```python
        self._prev_mid_yes: dict[str, float] = {}
```

- [ ] **Step 2: Pass and update `prev_mid_yes` in `_requote_market`**

In `_requote_market`, change the `compute_ladder(...)` call to pass the stored previous mid,
and store the current mid afterward. Replace:

```python
            desired = compute_ladder(
                self.cfg,
                mid_yes=mid_yes,
                time_to_expiry=tte,
                committed_side=committed,
                inventory_yes_qty=yes_qty,
                inventory_no_qty=no_qty,
                timeframe=market.timeframe,
                asset=market.asset,
                velocity_short=velo_short,
                velocity_long=velo_long,
            )
            self.exec.sync(market_id, desired)
```

with:

```python
            desired = compute_ladder(
                self.cfg,
                mid_yes=mid_yes,
                time_to_expiry=tte,
                prev_mid_yes=self._prev_mid_yes.get(market_id),
                committed_side=committed,
                inventory_yes_qty=yes_qty,
                inventory_no_qty=no_qty,
                timeframe=market.timeframe,
                asset=market.asset,
                velocity_short=velo_short,
                velocity_long=velo_long,
            )
            self._prev_mid_yes[market_id] = mid_yes
            self.exec.sync(market_id, desired)
```

- [ ] **Step 3: Run the loop tests**

Run: `.venv/bin/pytest tests/test_quoter_loop.py -v`
Expected: PASS. The change is additive and backward-compatible. If a test asserts the exact
kwargs of a mocked `compute_ladder`, update it to allow the new `prev_mid_yes` kwarg.

- [ ] **Step 4: Commit**

```bash
git add quoter/quoter_loop.py tests/test_quoter_loop.py
git commit -m "feat(phase-15): thread prev_mid_yes through quoter loop for buy-on-rise"
```

---

## Task 5: Remove dead phase-11/12/13/14 config knobs

**Files:**
- Modify: `quoter/config.py`
- Modify: `tests/test_config.py`

Now that `compute_ladder` and the backtest no longer use them, delete the dead strategy
knobs. **Verify zero references before removing each** (some are shared infra — keep those).

- [ ] **Step 1: Grep each candidate for remaining references**

Run for each name below:
```bash
grep -rn '<NAME>' quoter/ tests/
```
Candidates to REMOVE (expect references only in `quoter/config.py` after Tasks 1-4):
`entry_cutoff_frac`, `cheap_tail_levels`, `budget_per_market_usd`, `quote_base_size`,
`self_cross_buffer`, `tight_cluster_levels`, `tight_cluster_multiplier`,
`directional_filter_enabled`, `directional_high_threshold`, `directional_low_threshold`,
`directional_size_skew_enabled`, `directional_skew_coef`, `late_window_sec`,
`late_window_size_multiplier`, `late_window_dominant_threshold`, `timing_curve_5m`,
`timing_curve_15m`, `polarized_threshold`, `polarized_cheap_side_pct`,
`conviction_budget_multiplier`, `conviction_window_open_max_sec`, `conviction_assets`,
`conviction_extreme_mid_threshold`, `velocity_neutral_threshold`, `conviction_min_velocity`,
`velocity_buffer_max_age_sec`, `ladder_levels`.

KEEP (still referenced by `quoter_loop.py` / risk / infra): `velocity_short_lookback_sec`,
`velocity_long_lookback_sec`, `max_inventory_skew_shares`, `requote_*`, all paper-fill,
risk, WS, paths, and endpoint knobs.

For any candidate that still has references **outside** `quoter/config.py`, leave it in place
and note why (do not break a live reference).

- [ ] **Step 2: Delete the confirmed-unreferenced knobs from `quoter/config.py`**

Remove the dataclass fields (and their comment blocks) for every candidate from Step 1 that
had references only in `quoter/config.py`. This includes the Phase-9/11/12/13 comment
sections for the ladder, tight cluster, directional filter, directional skew, late-window,
timing curves, polarized, and conviction blocks, plus the legacy `entry_cutoff_frac` line
added in Task 1.

- [ ] **Step 3: Update `tests/test_config.py::test_defaults`**

The `test_defaults` method asserts `c.ladder_levels == 50`. Remove that line (the knob is
gone):

```python
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            c = Config.from_env()
        assert c.mode == "shadow"
        assert c.is_shadow
        assert not c.is_paper and not c.is_live
        assert c.bankroll_usd == 100.0
```

- [ ] **Step 4: Run the FULL test suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (all tests green). If any module still imports a removed knob, either it was a
KEEP candidate (restore it) or update the reference.

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_config.py
git commit -m "refactor(phase-15): remove dead phase-11/12/13/14 config knobs"
```

---

## Self-Review

**Spec coverage:**
- One-sided favorite selection → Task 2 (`_pick_favorite_side`, `test_only_favorite_side`). ✅
- Late-window gate (`entry_start_frac`) → Task 2 (`test_too_early_no_quotes`). ✅
- Favorite min price / dead-zone → Task 2 (`test_below_min_price_no_quotes`, `test_dead_zone_no_quotes`). ✅
- Price ceiling 0.95 + sweep → Task 1 (default), Task 3 (`sweep`). ✅
- Buy-on-rise (anti-knife) → Task 2 (`test_falling_favorite_suppressed`/`test_rising_favorite_allowed`), threaded in Task 3 (engine) + Task 4 (loop). ✅
- Binance momentum confirm → Task 2 (`test_velocity_disagree_blocks`/`_agree`/`_none`). ✅
- Certainty-scaled sizing → Task 2 (`_certainty_size`, `test_certainty_size_monotonic`). ✅
- Certainty-scaled per-market cap → Task 2 (`test_per_market_cap_stops_adds`). ✅
- No sells / hold to resolution → no sell path exists anywhere (verified); nothing added. ✅
- Backtest vs recorded −$282.53 + invariant (real `compute_ladder`) → Task 3. ✅
- Remove phase-11/12/13/14 machinery → Task 5. ✅

**Placeholder scan:** No TBD/TODO; every code step has complete code. ✅

**Type consistency:** `compute_ladder` signature is identical across Task 2 (definition),
Task 3 (engine call), Task 4 (loop call); `Quote(side, price, size)`, `_certainty_size`,
`_certainty`, `_favorite_ladder`, `_pick_favorite_side` names are consistent. ✅

**Deviation from spec flagged at top:** `dead_zone_half_width` folded into
`favorite_min_price`; favorite ladder top bid at `fav_price` (not −1c). Both documented.

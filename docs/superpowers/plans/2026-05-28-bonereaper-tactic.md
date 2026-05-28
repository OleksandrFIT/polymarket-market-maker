# Bonereaper-style Tactic + Offline Backtest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chase-the-favorite quoting tactic with Bonereaper-style early-entry + price-cap, and build an offline replay backtest to validate it deterministically on real Polymarket price histories.

**Architecture:** Two parts. (1) Tactic changes are config-driven knobs read by the existing pure function `compute_ladder` in `quoter/strategy/ladder.py`. (2) A new offline package `quoter/backtest/` fetches real per-token price series from the CLOB `prices-history` endpoint, replays `compute_ladder` through a deterministic fill model, and prints an A/B PnL table. The engine calls the real `compute_ladder` so the backtest tests production strategy code, not a copy.

**Tech Stack:** Python 3, dataclasses, stdlib `urllib`/`json`/`sqlite3`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-28-bonereaper-tactic-design.md`

**Test command:** `.venv/bin/pytest <path> -v` (asyncio_mode=auto, testpaths=tests).

---

## Task 1: Add tactic config knobs

**Files:**
- Modify: `quoter/config.py:46-58`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_new_tactic_defaults():
    from quoter.config import Config
    c = Config()
    assert c.entry_cutoff_frac == 0.50
    assert c.max_entry_price == 0.60
    # chase-the-favorite layers OFF by default (proven to hurt)
    assert c.directional_filter_enabled is False
    assert c.directional_size_skew_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py::test_new_tactic_defaults -v`
Expected: FAIL (AttributeError: entry_cutoff_frac, or assert False on directional defaults)

- [ ] **Step 3: Edit config**

In `quoter/config.py`, change the two directional defaults (lines 49 and 57):

```python
    directional_filter_enabled: bool = False  # Phase-14: data showed it loads losing side
```
```python
    directional_size_skew_enabled: bool = False  # Phase-14: off (amplified losing side)
```

Then add a new block right after the directional size skew block (after line 58):

```python
    # ── Phase-14 entry discipline (Bonereaper-style early-entry + price-cap) ──
    # Data (2026-05-28): -$208 of -$247 loss came from fills after 66% of the
    # window; -$162 from fills above price 0.60 (chasing the favorite into
    # whipsaw). Stop adding inventory late, and never bid above max_entry_price.
    entry_cutoff_frac: float = 0.50   # no new quotes after this fraction of window
    max_entry_price: float = 0.60     # drop any quote priced above this
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py::test_new_tactic_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_config.py
git commit -m "feat(phase-14): add entry-discipline config knobs, disable chase-the-favorite"
```

---

## Task 2: Late-stop in compute_ladder

**Files:**
- Modify: `quoter/strategy/ladder.py:82-83` (after `window_frac` is computed)
- Test: `tests/test_ladder.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ladder.py`:

```python
class TestEntryCutoff:
    def test_no_quotes_after_cutoff(self):
        cfg = replace(CFG, entry_cutoff_frac=0.50)
        # 5m window = 300s. At tte=60s we are 240/300 = 80% in → past cutoff.
        out = compute_ladder(cfg, mid_yes=0.50, time_to_expiry=60,
                             timeframe="5m")
        assert out == []

    def test_quotes_before_cutoff(self):
        cfg = replace(CFG, entry_cutoff_frac=0.50)
        # tte=240s → 60/300 = 20% in → before cutoff.
        out = compute_ladder(cfg, mid_yes=0.50, time_to_expiry=240,
                             timeframe="5m")
        assert len(out) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ladder.py::TestEntryCutoff -v`
Expected: FAIL on `test_no_quotes_after_cutoff` (returns quotes, not [])

- [ ] **Step 3: Add the cutoff check**

In `quoter/strategy/ladder.py`, immediately after line 82 (`window_frac = min(1.0, time_into_window / window_length_sec)`), add:

```python

    # ── Phase-14: late-stop — no NEW inventory after entry cutoff ──
    if window_frac > cfg.entry_cutoff_frac:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ladder.py::TestEntryCutoff -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/strategy/ladder.py tests/test_ladder.py
git commit -m "feat(phase-14): late-stop — no new quotes past entry_cutoff_frac"
```

---

## Task 3: Price-cap in compute_ladder

**Files:**
- Modify: `quoter/strategy/ladder.py:118-125` (the assembly/return block)
- Test: `tests/test_ladder.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ladder.py`:

```python
class TestPriceCap:
    def test_no_quote_above_cap(self):
        cfg = replace(CFG, max_entry_price=0.60)
        # mid_yes=0.85 → YES bids near 0.84, 0.83... must all be dropped;
        # NO bids near 0.14 survive.
        out = compute_ladder(cfg, mid_yes=0.85, time_to_expiry=240,
                             timeframe="5m")
        assert out, "expected some (cheap NO) quotes"
        assert all(q.price <= 0.60 for q in out), \
            f"quote above cap: {[q for q in out if q.price > 0.60]}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ladder.py::TestPriceCap -v`
Expected: FAIL (YES bids at 0.8x exceed cap)

- [ ] **Step 3: Add the price-cap filter**

In `quoter/strategy/ladder.py`, in the assembly block, change the final return so the cap is applied before self-cross dropping. Replace lines 118-125 (the `out = _layer_a_continuous(...)` through `return _drop_self_crossing(...)`) with:

```python
    out = _layer_a_continuous(
        cfg, mid_yes, mid_no, yes_mult, no_mult, skip_yes_a, skip_no_a, budget,
    )
    # Cheap-tail uses INVENTORY skip only (not directional filter)
    out.extend(_layer_b_cheap_tail(
        cfg, mid_yes, mid_no, skip_yes_inv, skip_no_inv, budget, timing_mult,
    ))
    # ── Phase-14: price-cap — never bid above max_entry_price (anti-chase) ──
    out = [q for q in out if q.price <= cfg.max_entry_price]
    return _drop_self_crossing(out, cfg.self_cross_buffer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ladder.py::TestPriceCap -v`
Expected: PASS

- [ ] **Step 5: Run the full ladder suite (regression check)**

Run: `.venv/bin/pytest tests/test_ladder.py -v`
Expected: All PASS. NOTE: pre-existing tests that asserted directional-filter or skew behavior may now fail because those defaults flipped in Task 1. If a test explicitly sets `directional_filter_enabled=True`/`directional_size_skew_enabled=True` it still works; if a test relied on the OLD default being True, update that test to set the flag explicitly. Do not change production defaults to satisfy old tests.

- [ ] **Step 6: Commit**

```bash
git add quoter/strategy/ladder.py tests/test_ladder.py
git commit -m "feat(phase-14): price-cap — drop quotes above max_entry_price"
```

---

## Task 4: Backtest data models

**Files:**
- Create: `quoter/backtest/__init__.py`
- Create: `quoter/backtest/models.py`
- Test: `tests/test_backtest_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_models.py`:

```python
from quoter.backtest.models import MarketWindow, PricePoint, BacktestResult


def test_market_window_fields():
    m = MarketWindow(market_id="0xabc", asset="BTC", timeframe="5m",
                     open_ts=100, expire_ts=400, winning_side="YES",
                     yes_token="tok")
    assert m.window_length == 300

def test_backtest_result_aggregate():
    r = BacktestResult(market_id="0xabc", pnl=12.5, yes_qty=10, no_qty=3,
                       total_cost=5.0, n_fills=4)
    assert r.pnl == 12.5
    assert r.n_fills == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest_models.py -v`
Expected: FAIL (ModuleNotFoundError: quoter.backtest)

- [ ] **Step 3: Create the package + models**

Create `quoter/backtest/__init__.py`:

```python
```
(empty file)

Create `quoter/backtest/models.py`:

```python
"""Data models for the offline backtest harness."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketWindow:
    """One resolved market to replay."""

    market_id: str
    asset: str
    timeframe: str
    open_ts: int
    expire_ts: int
    winning_side: str  # "YES" | "NO"
    yes_token: str

    @property
    def window_length(self) -> int:
        return self.expire_ts - self.open_ts


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A single (timestamp, YES-price) sample from CLOB prices-history."""

    t: int
    yes_price: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """PnL outcome of replaying one market under one config."""

    market_id: str
    pnl: float
    yes_qty: float
    no_qty: float
    total_cost: float
    n_fills: int
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/backtest/__init__.py quoter/backtest/models.py tests/test_backtest_models.py
git commit -m "feat(backtest): data models (MarketWindow, PricePoint, BacktestResult)"
```

---

## Task 5: Deterministic fill model

**Files:**
- Create: `quoter/backtest/fillsim.py`
- Test: `tests/test_backtest_fillsim.py`

A resting BUY bid fills when the market price of its side reaches its bid level
over the interval. YES side price = `yes_price`; NO side price = `1 - yes_price`.
Rule: filled (full size) if the side's price dips to or below the bid price
across `[yes_now → yes_next]`. Identical for every config → fair A/B.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_fillsim.py`:

```python
from quoter.strategy.ladder import Quote
from quoter.backtest.fillsim import simulate_interval_fills


def test_yes_bid_fills_when_price_dips_to_it():
    # YES bid at 0.40; YES price moves 0.50 -> 0.38 (dips below 0.40) → fills
    q = Quote("YES", 0.40, 10)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.38)
    assert fills == [("YES", 0.40, 10)]

def test_yes_bid_no_fill_when_price_stays_above():
    q = Quote("YES", 0.40, 10)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.45)
    assert fills == []

def test_no_bid_fills_when_no_price_dips():
    # NO bid at 0.40; NO price = 1 - yes. yes 0.50 -> 0.65 => no 0.50 -> 0.35
    # (dips below 0.40) → fills
    q = Quote("NO", 0.40, 7)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.65)
    assert fills == [("NO", 0.40, 7)]

def test_no_bid_no_fill():
    q = Quote("NO", 0.40, 7)
    fills = simulate_interval_fills([q], yes_now=0.50, yes_next=0.55)
    assert fills == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest_fillsim.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement fillsim**

Create `quoter/backtest/fillsim.py`:

```python
"""Deterministic fill model for the offline backtest.

Coarse on purpose (CLOB history is ~1 sample/min): a resting BUY bid fills
in full when its side's market price dips to/below the bid price over the
interval. Identical rule for every config so A/B comparisons are fair.
"""

from __future__ import annotations

from quoter.strategy.ladder import Quote

Fill = tuple[str, float, int]  # (side, price, size)


def simulate_interval_fills(
    quotes: list[Quote], yes_now: float, yes_next: float,
) -> list[Fill]:
    """Return the quotes that fill as YES price moves yes_now -> yes_next."""
    yes_low = min(yes_now, yes_next)
    no_low = 1.0 - max(yes_now, yes_next)
    fills: list[Fill] = []
    for q in quotes:
        side_low = yes_low if q.side == "YES" else no_low
        if side_low <= q.price:
            fills.append((q.side, q.price, q.size))
    return fills
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest_fillsim.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/backtest/fillsim.py tests/test_backtest_fillsim.py
git commit -m "feat(backtest): deterministic interval fill model"
```

---

## Task 6: Replay engine

**Files:**
- Create: `quoter/backtest/engine.py`
- Test: `tests/test_backtest_engine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_engine.py`:

```python
from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.engine import run_market


def _win_market():
    # 5m window, YES wins. open=0, expire=300.
    return MarketWindow(market_id="0xwin", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")

def test_buying_winner_cheap_is_profitable():
    cfg = Config(max_inventory_skew_shares=200)
    # YES sits cheap early (0.30) then climbs to 0.99 → our YES bids at <=0.30
    # fill early, resolve to $1 each → positive PnL.
    series = [PricePoint(0, 0.30), PricePoint(60, 0.30),
              PricePoint(120, 0.55), PricePoint(180, 0.99)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty > 0
    assert res.pnl > 0

def test_no_fills_no_pnl():
    cfg = Config(max_inventory_skew_shares=200)
    # Flat price, no dips below any bid that survives cap → essentially no PnL
    series = [PricePoint(0, 0.50), PricePoint(60, 0.50)]
    res = run_market(cfg, _win_market(), series)
    assert res.pnl == res.yes_qty * 1.0 - res.total_cost
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest_engine.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement engine**

Create `quoter/backtest/engine.py`:

```python
"""Replay one market window through the real compute_ladder + fill model."""

from __future__ import annotations

from quoter.config import Config
from quoter.strategy.ladder import compute_ladder
from quoter.backtest.fillsim import simulate_interval_fills
from quoter.backtest.models import BacktestResult, MarketWindow, PricePoint


def run_market(
    cfg: Config, window: MarketWindow, series: list[PricePoint],
) -> BacktestResult:
    """Replay `series` for `window` under `cfg`; return PnL at resolution."""
    yes_qty = no_qty = 0.0
    total_cost = 0.0
    n_fills = 0

    for i in range(len(series) - 1):
        now, nxt = series[i], series[i + 1]
        tte = window.expire_ts - now.t
        if tte <= 0:
            break
        desired = compute_ladder(
            cfg,
            mid_yes=now.yes_price,
            time_to_expiry=float(tte),
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

    win_shares = yes_qty if window.winning_side == "YES" else no_qty
    pnl = win_shares * 1.0 - total_cost
    return BacktestResult(
        market_id=window.market_id, pnl=pnl, yes_qty=yes_qty,
        no_qty=no_qty, total_cost=total_cost, n_fills=n_fills,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/backtest/engine.py tests/test_backtest_engine.py
git commit -m "feat(backtest): replay engine over real compute_ladder"
```

---

## Task 7: Data fetch + cache

**Files:**
- Create: `quoter/backtest/fetch.py`
- Test: `tests/test_backtest_fetch.py`

Network call kept in one thin function; parsing + DB loading are pure and tested.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_fetch.py`:

```python
from quoter.backtest.fetch import parse_price_history
from quoter.backtest.models import PricePoint


def test_parse_price_history():
    raw = {"history": [{"t": 100, "p": 0.485}, {"t": 160, "p": 0.295}]}
    pts = parse_price_history(raw)
    assert pts == [PricePoint(100, 0.485), PricePoint(160, 0.295)]

def test_parse_empty_history():
    assert parse_price_history({"history": []}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest_fetch.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement fetch**

Create `quoter/backtest/fetch.py`:

```python
"""Load resolved markets from state.db and fetch their CLOB price series.

Price series are cached to quoter/backtest/data/<market_id>.json so we hit
the API only once per market.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request

from quoter.backtest.models import MarketWindow, PricePoint

_CLOB = "https://clob.polymarket.com/prices-history"
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_markets_from_db(db_path: str = "state.db") -> list[MarketWindow]:
    """All RESOLVED markets with a known winning side and yes_token."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT market_id, asset, timeframe, open_ts, expire_ts, "
        "winning_side, yes_token FROM markets "
        "WHERE status='RESOLVED' AND winning_side IS NOT NULL "
        "AND yes_token IS NOT NULL"
    ).fetchall()
    con.close()
    return [
        MarketWindow(
            market_id=r["market_id"], asset=r["asset"],
            timeframe=r["timeframe"], open_ts=int(r["open_ts"]),
            expire_ts=int(r["expire_ts"]), winning_side=r["winning_side"],
            yes_token=r["yes_token"],
        )
        for r in rows
    ]


def parse_price_history(raw: dict) -> list[PricePoint]:
    """Convert CLOB prices-history JSON to PricePoint list."""
    return [PricePoint(int(h["t"]), float(h["p"])) for h in raw.get("history", [])]


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def fetch_price_series(window: MarketWindow, *, use_cache: bool = True) -> list[PricePoint]:
    """Fetch (or load cached) YES-token price series for one market window."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    cache = os.path.join(_DATA_DIR, f"{window.market_id}.json")
    if use_cache and os.path.exists(cache):
        with open(cache) as f:
            return parse_price_history(json.load(f))
    url = (
        f"{_CLOB}?market={window.yes_token}"
        f"&startTs={window.open_ts - 60}&endTs={window.expire_ts + 60}&fidelity=1"
    )
    raw = _http_get_json(url)
    with open(cache, "w") as f:
        json.dump(raw, f)
    return parse_price_history(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest_fetch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/backtest/fetch.py tests/test_backtest_fetch.py
git commit -m "feat(backtest): load markets from db + fetch/cache CLOB price series"
```

---

## Task 8: Runner + A/B table

**Files:**
- Create: `quoter/backtest/run_backtest.py`
- Test: `tests/test_backtest_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backtest_runner.py`:

```python
from dataclasses import replace
from quoter.config import Config
from quoter.backtest.models import MarketWindow, PricePoint
from quoter.backtest.run_backtest import run_config


def _market():
    return MarketWindow(market_id="0xa", asset="BTC", timeframe="5m",
                        open_ts=0, expire_ts=300, winning_side="YES",
                        yes_token="t")

def test_run_config_aggregates_pnl():
    cfg = Config(max_inventory_skew_shares=200)
    series = {"0xa": [PricePoint(0, 0.30), PricePoint(60, 0.30),
                      PricePoint(120, 0.99)]}
    summary = run_config(cfg, [_market()], series)
    assert summary.n_markets == 1
    assert summary.total_pnl == summary.results[0].pnl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backtest_runner.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement runner**

Create `quoter/backtest/run_backtest.py`:

```python
"""Run one or more configs over cached market windows; print an A/B table.

CLI:  .venv/bin/python -m quoter.backtest.run_backtest
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from quoter.config import Config
from quoter.backtest.engine import run_market
from quoter.backtest.fetch import fetch_price_series, load_markets_from_db
from quoter.backtest.models import BacktestResult, MarketWindow, PricePoint


@dataclass
class ConfigSummary:
    name: str
    n_markets: int
    total_pnl: float
    wins: int
    worst: float
    results: list[BacktestResult] = field(default_factory=list)


def run_config(
    cfg: Config, markets: list[MarketWindow],
    series_by_market: dict[str, list[PricePoint]], name: str = "cfg",
) -> ConfigSummary:
    results: list[BacktestResult] = []
    for m in markets:
        series = series_by_market.get(m.market_id, [])
        if len(series) < 2:
            continue
        results.append(run_market(cfg, m, series))
    total = sum(r.pnl for r in results)
    wins = sum(1 for r in results if r.pnl > 0)
    worst = min((r.pnl for r in results), default=0.0)
    return ConfigSummary(name, len(results), total, wins, worst, results)


def _print_table(summaries: list[ConfigSummary]) -> None:
    print(f"\n{'config':28} {'n':>4} {'total PnL':>12} {'win-rate':>9} {'worst':>10}")
    print("-" * 66)
    for s in summaries:
        wr = f"{s.wins}/{s.n_markets}"
        print(f"{s.name:28} {s.n_markets:>4} {s.total_pnl:>12.2f} {wr:>9} {s.worst:>10.2f}")


def main() -> None:
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}

    baseline = replace(
        Config(), directional_filter_enabled=True,
        directional_size_skew_enabled=True, entry_cutoff_frac=1.0,
        max_entry_price=0.99,
    )
    new_tactic = Config()  # phase-14 defaults

    summaries = [
        run_config(baseline, markets, series_by_market, "baseline(phase-13)"),
        run_config(new_tactic, markets, series_by_market, "new(early+cap)"),
    ]
    _print_table(summaries)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backtest_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/backtest/run_backtest.py tests/test_backtest_runner.py
git commit -m "feat(backtest): runner with baseline vs new-tactic A/B table"
```

---

## Task 9: Run baseline vs new tactic, record result

**Files:**
- Create: `reports/2026-05-28_backtest-baseline-vs-new.md`

- [ ] **Step 1: Fetch data + run the A/B**

Run: `.venv/bin/python -m quoter.backtest.run_backtest`
Expected: a printed table with two rows (baseline vs new). First run populates `quoter/backtest/data/`.

- [ ] **Step 2: Sanity-check the baseline**

The baseline row replays the phase-13 config. Its total PnL on the 16 markets
should be clearly negative (directionally consistent with the live −$247.51;
exact value differs — coarse history + deterministic fills). If baseline is
strongly positive, the fill model or winning-side mapping is wrong — STOP and
debug `engine.py`/`fillsim.py` before trusting any comparison.

- [ ] **Step 3: Write the result report**

Create `reports/2026-05-28_backtest-baseline-vs-new.md` with: the printed table,
the per-market breakdown for the new tactic, and a one-paragraph verdict
(does new beat baseline on total PnL AND worst-case?). Note the sample size and
the caveat that fills are simulated on coarse history.

- [ ] **Step 4: Commit**

```bash
git add reports/2026-05-28_backtest-baseline-vs-new.md quoter/backtest/data/.gitkeep
git commit -m "test(backtest): baseline vs new-tactic A/B result on 16 markets"
```

---

## Task 10: Expand sample + threshold sweep (anti-overfit)

**Files:**
- Modify: `quoter/backtest/run_backtest.py` (add `sweep` entry point)

- [ ] **Step 1: Add a sweep function**

In `quoter/backtest/run_backtest.py`, add below `main`:

```python
def sweep() -> None:
    """Grid over entry_cutoff_frac x max_entry_price; print PnL per combo."""
    markets = load_markets_from_db()
    series_by_market = {m.market_id: fetch_price_series(m) for m in markets}
    summaries: list[ConfigSummary] = []
    for cutoff in (0.40, 0.50, 0.60, 0.75):
        for cap in (0.50, 0.55, 0.60, 0.70):
            cfg = replace(Config(), entry_cutoff_frac=cutoff, max_entry_price=cap)
            summaries.append(
                run_config(cfg, markets, series_by_market,
                           f"cut={cutoff} cap={cap}")
            )
    summaries.sort(key=lambda s: s.total_pnl, reverse=True)
    _print_table(summaries)
```

Then change the CLI dispatch at the bottom from `main()` to:

```python
if __name__ == "__main__":
    import sys
    sweep() if "--sweep" in sys.argv else main()
```

- [ ] **Step 2: Verify it runs**

Run: `.venv/bin/python -m quoter.backtest.run_backtest --sweep`
Expected: a sorted table of 16 combos by total PnL.

- [ ] **Step 3: (Manual) Expand the market sample toward ≥50**

The success criterion requires ≥50 markets to avoid overfitting the 16 we have.
If `load_markets_from_db()` returns <50, gather more resolved BTC/ETH up-down
markets: let the live bot run longer to accumulate resolved rows in state.db,
OR add a `fetch_resolved_markets_from_api()` helper that pulls historical
up-down markets + their winning side from the Polymarket markets/CLOB API and
feeds `MarketWindow`s directly (no DB dependency). Re-run the sweep on the
larger set before locking thresholds.

- [ ] **Step 4: Commit**

```bash
git add quoter/backtest/run_backtest.py
git commit -m "feat(backtest): entry_cutoff x max_entry_price sweep"
```

---

## Self-review notes

- **Spec coverage:** Component 1 (harness) = Tasks 4-8; Component 2 (tactic) = Tasks 1-3; Component 3 (tuning + success criteria) = Tasks 9-10. All covered.
- **Caveat carried through:** simulated-fills/coarse-history warning appears in engine docstring and Tasks 9-10.
- **Live-affecting item #4 (early at-mid bid)** from the spec is intentionally NOT in this plan — it does not affect the backtest (fill model fills regardless) and must be validated live; it should be a separate follow-up so it does not silently change live behavior. Flag to the user at execution time.
- **Type consistency:** `MarketWindow`, `PricePoint`, `BacktestResult`, `ConfigSummary`, `simulate_interval_fills`, `run_market`, `run_config` names are used consistently across tasks.

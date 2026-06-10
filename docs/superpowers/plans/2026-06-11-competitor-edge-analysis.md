# Competitor Edge Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure from Bonereaper's public BTC 5m trades whether his laddered pair-spread edge stays positive net of his naked-leg losses (the go/no-go for building a laddered re-quoter).

**Architecture:** A pure, unit-tested core (`quoter/analysis/competitor.py`) reconstructs per-window P&L (pair edge + naked-leg P&L) and aggregates it into a verdict; a thin I/O script (`scripts/analyze_competitor.py`) pulls his trades + each window's resolution and prints the report. Read-only — no trading.

**Tech Stack:** Python 3.13, stdlib `urllib`/`json` (matches existing scripts), pytest. Data: `data-api.polymarket.com/activity` (his BUY trades), `clob.polymarket.com/markets/<conditionId>` (winner per window).

**Spec:** `docs/superpowers/specs/2026-06-11-competitor-edge-analysis-design.md`

---

## File structure

- `quoter/analysis/__init__.py` — new package marker (empty).
- `quoter/analysis/competitor.py` — pure logic: `Trade`, `WindowResult`, `Report`, `reconstruct_window`, `aggregate`. No I/O.
- `scripts/analyze_competitor.py` — I/O glue: fetch trades + resolutions, call pure logic, print report.
- `tests/test_competitor_analysis.py` — unit tests for the pure logic.

---

### Task 1: Pure per-window reconstruction

**Files:**
- Create: `quoter/analysis/__init__.py` (empty)
- Create: `quoter/analysis/competitor.py`
- Test: `tests/test_competitor_analysis.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_competitor_analysis.py`:

```python
"""Competitor edge analysis — pure reconstruction + aggregation (no I/O)."""

from quoter.analysis.competitor import (
    Trade, WindowResult, aggregate, reconstruct_window,
)


def test_balanced_hedge_up_wins():
    # Up 5@0.45 + Down 5@0.50 = pair 0.95; Up wins. Hedge edge only, no naked.
    r = reconstruct_window("w1",
        [Trade("Up", 5, 0.45), Trade("Down", 5, 0.50)], "Up")
    assert r.matched == 5
    assert abs(r.pair_cost - 0.95) < 1e-9
    assert abs(r.pair_pnl - 0.25) < 1e-9        # 5 * (1 - 0.95)
    assert r.naked_shares == 0
    assert r.naked_pnl == 0.0
    assert abs(r.net - 0.25) < 1e-9


def test_balanced_hedge_is_direction_independent():
    # Same trades, Down wins → same pair_pnl (hedge doesn't care who wins).
    r = reconstruct_window("w2",
        [Trade("Up", 5, 0.45), Trade("Down", 5, 0.50)], "Down")
    assert abs(r.pair_pnl - 0.25) < 1e-9
    assert r.naked_pnl == 0.0


def test_imbalanced_naked_side_wins():
    # Up 10@0.30 + Down 5@0.40; Up wins. matched 5, naked 5 Up @0.30 wins.
    r = reconstruct_window("w3",
        [Trade("Up", 10, 0.30), Trade("Down", 5, 0.40)], "Up")
    assert abs(r.pair_cost - 0.70) < 1e-9
    assert abs(r.pair_pnl - 1.50) < 1e-9        # 5 * (1 - 0.70)
    assert r.naked_shares == 5 and r.naked_side == "Up"
    assert abs(r.naked_pnl - 3.50) < 1e-9       # 5 * (1 - 0.30)
    assert abs(r.net - 5.00) < 1e-9


def test_imbalanced_naked_side_loses():
    # Same trades, Down wins → naked Up loses.
    r = reconstruct_window("w4",
        [Trade("Up", 10, 0.30), Trade("Down", 5, 0.40)], "Down")
    assert abs(r.pair_pnl - 1.50) < 1e-9        # hedge still pays
    assert abs(r.naked_pnl - (-1.50)) < 1e-9    # 5 * (0 - 0.30)
    assert abs(r.net - 0.00) < 1e-9


def test_pure_one_sided_window_loses():
    # Only Up 5@0.40, Down resolves winner → fully naked loss, no pair.
    r = reconstruct_window("w5", [Trade("Up", 5, 0.40)], "Down")
    assert r.matched == 0 and r.pair_pnl == 0.0
    assert r.naked_shares == 5 and r.naked_side == "Up"
    assert abs(r.naked_pnl - (-2.00)) < 1e-9    # 5 * (0 - 0.40)
    assert abs(r.net - (-2.00)) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_competitor_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'quoter.analysis'`

- [ ] **Step 3: Create the package marker**

Create `quoter/analysis/__init__.py` as an empty file (0 bytes).

- [ ] **Step 4: Implement the pure reconstruction**

Create `quoter/analysis/competitor.py`:

```python
"""Pure reconstruction of a competitor's per-window P&L from his trades.

No I/O. Given his BUY trades for one BTC 5m window and the window's winning side,
split his realized P&L into the HEDGE edge (matched pairs bought < $1) and the
NAKED-leg P&L (the unmatched side, paid off 1/0 at resolution). The whole point is
to see whether the hedge edge survives the naked legs. Assumes hold-to-resolution
(competitor sells ~0%), so resolution payout (1/0) equals his realized value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Trade:
    outcome: str   # "Up" | "Down"
    size: float
    price: float


@dataclass
class WindowResult:
    window_id: str
    up_shares: float
    up_avg: float
    down_shares: float
    down_avg: float
    matched: float
    pair_cost: float
    naked_shares: float
    naked_side: str | None
    naked_avg: float
    winning_side: str
    pair_pnl: float
    naked_pnl: float
    net: float
    spend: float


def reconstruct_window(window_id: str, trades: list[Trade], winning_side: str) -> WindowResult:
    """Split one window's P&L into hedge edge + naked-leg P&L."""
    up_sz = sum(t.size for t in trades if t.outcome == "Up")
    up_cost = sum(t.size * t.price for t in trades if t.outcome == "Up")
    dn_sz = sum(t.size for t in trades if t.outcome == "Down")
    dn_cost = sum(t.size * t.price for t in trades if t.outcome == "Down")
    up_avg = up_cost / up_sz if up_sz else 0.0
    dn_avg = dn_cost / dn_sz if dn_sz else 0.0

    matched = min(up_sz, dn_sz)
    pair_cost = (up_avg + dn_avg) if (up_sz and dn_sz) else 0.0
    pair_pnl = matched * (1.0 - pair_cost) if matched > 0 else 0.0

    naked_shares = abs(up_sz - dn_sz)
    if up_sz > dn_sz:
        naked_side, naked_avg = "Up", up_avg
    elif dn_sz > up_sz:
        naked_side, naked_avg = "Down", dn_avg
    else:
        naked_side, naked_avg = None, 0.0
    payout = 1.0 if naked_side == winning_side else 0.0
    naked_pnl = naked_shares * (payout - naked_avg) if naked_side else 0.0

    return WindowResult(
        window_id=window_id, up_shares=up_sz, up_avg=up_avg,
        down_shares=dn_sz, down_avg=dn_avg, matched=matched, pair_cost=pair_cost,
        naked_shares=naked_shares, naked_side=naked_side, naked_avg=naked_avg,
        winning_side=winning_side, pair_pnl=pair_pnl, naked_pnl=naked_pnl,
        net=pair_pnl + naked_pnl, spend=up_cost + dn_cost,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_competitor_analysis.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add quoter/analysis/__init__.py quoter/analysis/competitor.py tests/test_competitor_analysis.py
git commit -m "feat(analysis): pure per-window competitor P&L reconstruction (hedge vs naked split)"
```

---

### Task 2: Aggregation + verdict

**Files:**
- Modify: `quoter/analysis/competitor.py` (append `Report` + `aggregate`)
- Test: `tests/test_competitor_analysis.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_competitor_analysis.py`:

```python
def test_aggregate_strong_build():
    # 3 windows: net +0.25, +5.00, 0.00. pair total > 0, 2/3 net-positive.
    rs = [
        reconstruct_window("w1", [Trade("Up", 5, 0.45), Trade("Down", 5, 0.50)], "Up"),
        reconstruct_window("w3", [Trade("Up", 10, 0.30), Trade("Down", 5, 0.40)], "Up"),
        reconstruct_window("w4", [Trade("Up", 10, 0.30), Trade("Down", 5, 0.40)], "Down"),
    ]
    rep = aggregate(rs)
    assert rep.n_windows == 3 and rep.n_hedged == 3
    assert abs(rep.total_pair_pnl - 3.25) < 1e-9   # 0.25 + 1.50 + 1.50
    assert abs(rep.total_naked_pnl - 2.00) < 1e-9  # 0 + 3.50 - 1.50
    assert abs(rep.total_net - 5.25) < 1e-9
    assert rep.verdict == "STRONG BUILD"


def test_aggregate_dont_build_when_pair_edge_negative():
    # net positive ONLY because a naked leg got lucky; pair edge itself <= 0.
    rs = [
        reconstruct_window("a", [Trade("Up", 5, 0.60), Trade("Down", 5, 0.60)], "Up"),  # pair 1.20 -> pair_pnl -1.0
        reconstruct_window("b", [Trade("Up", 10, 0.20)], "Up"),                          # naked win +8.0
    ]
    rep = aggregate(rs)
    assert rep.total_pair_pnl <= 0
    assert rep.total_net > 0
    assert rep.verdict.startswith("DON'T BUILD")


def test_aggregate_empty():
    rep = aggregate([])
    assert rep.n_windows == 0 and rep.verdict == "NO DATA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_competitor_analysis.py -k aggregate -v`
Expected: FAIL with `ImportError: cannot import name 'aggregate'` (already imported at top) → collection error / NameError

- [ ] **Step 3: Implement aggregate + Report**

Append to `quoter/analysis/competitor.py`:

```python
@dataclass
class Report:
    n_windows: int
    n_hedged: int           # windows with both sides bought
    n_naked: int            # windows carrying any unmatched shares
    avg_pair_cost: float    # over hedged windows
    total_pair_pnl: float
    total_naked_pnl: float
    total_net: float
    net_per_window: float
    pct_windows_positive: float
    total_spend: float
    avg_size_per_window: float
    verdict: str


def aggregate(results: list[WindowResult]) -> Report:
    """Roll per-window results into the go/no-go report.

    Verdict (per spec):
      DON'T BUILD            if total_net <= 0
      DON'T BUILD (naked luck) if net > 0 but pair edge itself <= 0 (gambling)
      STRONG BUILD           if pair edge > 0 and > 50% of windows net-positive
      BUILD                  otherwise (pair edge > 0, net > 0)
    """
    n = len(results)
    if n == 0:
        return Report(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "NO DATA")

    hedged = [r for r in results if r.up_shares > 0 and r.down_shares > 0]
    naked = [r for r in results if r.naked_shares > 0]
    avg_pair = sum(r.pair_cost for r in hedged) / len(hedged) if hedged else 0.0
    tot_pair = sum(r.pair_pnl for r in results)
    tot_naked = sum(r.naked_pnl for r in results)
    tot_net = tot_pair + tot_naked
    pct_pos = 100.0 * sum(1 for r in results if r.net > 0) / n
    tot_spend = sum(r.spend for r in results)
    avg_size = sum(r.up_shares + r.down_shares for r in results) / n

    if tot_net <= 0:
        verdict = "DON'T BUILD"
    elif tot_pair <= 0:
        verdict = "DON'T BUILD (net positive only via naked luck)"
    elif pct_pos > 50:
        verdict = "STRONG BUILD"
    else:
        verdict = "BUILD"

    return Report(
        n_windows=n, n_hedged=len(hedged), n_naked=len(naked), avg_pair_cost=avg_pair,
        total_pair_pnl=tot_pair, total_naked_pnl=tot_naked, total_net=tot_net,
        net_per_window=tot_net / n, pct_windows_positive=pct_pos,
        total_spend=tot_spend, avg_size_per_window=avg_size, verdict=verdict,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_competitor_analysis.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add quoter/analysis/competitor.py tests/test_competitor_analysis.py
git commit -m "feat(analysis): aggregate competitor windows into go/no-go verdict"
```

---

### Task 3: I/O script — fetch trades + resolutions, print report

**Files:**
- Create: `scripts/analyze_competitor.py`

This task is I/O against live Polymarket APIs (no unit test; verified by a smoke run).
The API shapes are already confirmed:
- Trades: `https://data-api.polymarket.com/activity?user=<COMP>&limit=500&offset=N&sortBy=TIMESTAMP&sortDirection=DESC`
  → items with `type=="TRADE"`, `side=="BUY"`, `outcome` ("Up"/"Down"), `size`, `price`,
  `conditionId`, `slug` (e.g. `btc-updown-5m-1781124300`).
- Resolution: `https://clob.polymarket.com/markets/<conditionId>`
  → `closed: true`, `tokens: [{"outcome":"Up","price":1,"winner":true}, {"outcome":"Down","price":0,"winner":false}]`.

- [ ] **Step 1: Write the script**

Create `scripts/analyze_competitor.py`:

```python
"""Measure Bonereaper's BTC 5m edge: does his pair-spread survive his naked legs?

Read-only. Pulls his BUY trades (paginated), resolves each window's winner via the
CLOB market endpoint, reconstructs per-window P&L (hedge vs naked), and prints the
go/no-go report. Sample method B: ~TARGET_WINDOWS most-recent RESOLVED BTC 5m windows.

Run: .venv/bin/python scripts/analyze_competitor.py
"""

import json
import time
import urllib.request
from collections import defaultdict

from quoter.analysis.competitor import Trade, aggregate, reconstruct_window

COMP = "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
TARGET_WINDOWS = 120      # method B sample size
MAX_PAGES = 60            # pagination guard (60 * 500 = 30k trades)
PAGE = 500


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def fetch_btc_trades():
    """Paginate his activity; collect BTC 5m BUY trades grouped by (conditionId, slug).
    Stops once we have >= TARGET_WINDOWS distinct windows or hit MAX_PAGES."""
    by_window = defaultdict(lambda: {"slug": "", "trades": []})
    for page in range(MAX_PAGES):
        url = (f"https://data-api.polymarket.com/activity?user={COMP}"
               f"&limit={PAGE}&offset={page * PAGE}&sortBy=TIMESTAMP&sortDirection=DESC")
        try:
            acts = _get(url)
        except Exception as e:
            print(f"  page {page} fetch error: {e}")
            break
        if not acts:
            break
        for a in acts:
            slug = a.get("slug", "")
            if (a.get("type") == "TRADE" and a.get("side") == "BUY"
                    and slug.startswith("btc-updown-5m")
                    and a.get("outcome") in ("Up", "Down")):
                w = by_window[a["conditionId"]]
                w["slug"] = slug
                w["trades"].append(Trade(a["outcome"], float(a["size"]), float(a["price"])))
        if len(by_window) >= TARGET_WINDOWS:
            break
        time.sleep(0.2)   # gentle on the API
    return by_window


def fetch_winner(condition_id):
    """Return 'Up'/'Down' for a resolved window, or None if not resolved/unknown."""
    try:
        cm = _get(f"https://clob.polymarket.com/markets/{condition_id}")
    except Exception:
        return None
    if not cm.get("closed"):
        return None
    for t in cm.get("tokens", []):
        if t.get("winner") is True or float(t.get("price", 0)) >= 0.99:
            return t.get("outcome")
    return None


def main():
    print(f"Pulling Bonereaper BTC 5m trades (target {TARGET_WINDOWS} windows)...")
    by_window = fetch_btc_trades()
    print(f"  collected {len(by_window)} candidate windows; resolving winners...")

    results = []
    skipped = 0
    for cid, w in by_window.items():
        winner = fetch_winner(cid)
        if winner is None:
            skipped += 1
            continue
        results.append(reconstruct_window(w["slug"], w["trades"], winner))
        time.sleep(0.1)

    rep = aggregate(results)
    print("\n" + "=" * 60)
    print(f"COMPETITOR EDGE ANALYSIS — Bonereaper BTC 5m")
    print("=" * 60)
    print(f"Windows analyzed (resolved): {rep.n_windows}   (skipped unresolved: {skipped})")
    print(f"Hedged windows:  {rep.n_hedged}   avg pair ${rep.avg_pair_cost:.3f}")
    print(f"Naked windows:   {rep.n_naked}")
    print(f"Avg size/window: {rep.avg_size_per_window:.0f} shares   total spend ${rep.total_spend:,.0f}")
    print("-" * 60)
    print(f"  Pair P&L  (hedge edge): ${rep.total_pair_pnl:+,.2f}")
    print(f"  Naked P&L (directional): ${rep.total_naked_pnl:+,.2f}")
    print(f"  NET TOTAL:               ${rep.total_net:+,.2f}")
    print(f"  Net / window:            ${rep.net_per_window:+.3f}")
    print(f"  Windows net-positive:    {rep.pct_windows_positive:.0f}%")
    print("-" * 60)
    print(f"  VERDICT: {rep.verdict}")
    print("=" * 60)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-run the script**

Run: `.venv/bin/python scripts/analyze_competitor.py`
Expected: prints the report block with `Windows analyzed (resolved): N` where N > 0,
non-zero `Pair P&L` / `Naked P&L`, and a `VERDICT:` line (one of NO DATA / DON'T BUILD /
BUILD / STRONG BUILD). If `N == 0`, increase `MAX_PAGES` or check that recent BTC 5m
windows have resolved (very recent windows are still open).

- [ ] **Step 3: Commit**

```bash
git add scripts/analyze_competitor.py
git commit -m "feat(analysis): script to measure competitor BTC 5m hedge-vs-naked edge"
```

---

## Self-review

**Spec coverage:**
- Pure `reconstruct_window` + `WindowResult` (Task 1) ✓ — spec "Data model" + "P&L math".
- `aggregate` + `Report` + verdict thresholds (Task 2) ✓ — spec "Decision" + "Deliverable".
- I/O script: paginated trades, CLOB resolution, BTC-5m filter, report print (Task 3) ✓ — spec "Data flow" + "Architecture unit 2".
- Edge cases: unresolved excluded (`fetch_winner` returns None → skipped), pure one-sided (`test_pure_one_sided_window_loses`), perfectly matched (`naked_side=None` path), data truncation guard (`MAX_PAGES`, `skipped` count printed) ✓ — spec "Error / edge handling".
- Tests: balanced win/loss, naked win/loss, one-sided, aggregate split + verdict + empty ✓ — spec "Testing".
- Assumption hold-to-resolution documented in module docstring ✓.

**Placeholder scan:** none — all steps contain full code and exact commands.

**Type consistency:** `Trade(outcome,size,price)`, `WindowResult` fields, `Report` fields, and `reconstruct_window`/`aggregate` signatures are identical across Task 1, Task 2, and Task 3 usage. `fetch_winner` returns the same `"Up"/"Down"/None` that `reconstruct_window`'s `winning_side` expects.

# Pair-Completion Policy + Two-Mode Analysis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add taker pair-completion to our simulated MM policy and measure on the real collected book+tape data whether it raises our matched-pair fraction toward the competitor's ~85% and flips our queue-grounded edge% positive, comparing continuous vs near-end timing against a no-completion baseline.

**Architecture:** One pure decision function (`mm_complete.completion_buy`) + one pure book helper (`mm_book.best_ask`) + one analysis report (`scripts/_book_edge_complete.py`) that reuses `mm_book.queue_fill/best_mid`, `mm_policy.deep_ladder_quotes`, and `mm_tape.load_window`. Offline, zero trading; the collector is untouched.

**Tech Stack:** Python 3.13, stdlib, pytest. `.venv/bin/python`. Branch `master`. Reuses `quoter/research/`.

**Conventions:** side names `"Up"`/`"Down"`; book level `[price, size]`; CLOB asks are descending so `best_ask` = min price.

---

### Task 1: `best_ask` in mm_book

**Files:**
- Modify: `quoter/research/mm_book.py`
- Test: `tests/test_mm_book.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mm_book.py`:

```python
from quoter.research.mm_book import best_ask


def test_best_ask_is_min_ask_price():
    # CLOB returns asks descending -> [0] is NOT best ask
    asks = [[0.99, 100], [0.30, 10], [0.23, 8]]
    assert best_ask(asks) == 0.23
    assert best_ask([]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mm_book.py::test_best_ask_is_min_ask_price -q`
Expected: FAIL (ImportError: cannot import name 'best_ask').

- [ ] **Step 3: Add the implementation**

Add to `quoter/research/mm_book.py` (next to `best_mid`):

```python
def best_ask(ask_levels: list):
    """Best (lowest) ask price. CLOB /book returns asks descending, so reduce by min.
    Returns None if there are no asks."""
    return min((float(p) for p, _ in ask_levels), default=None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mm_book.py -q`
Expected: PASS (all mm_book tests, including the new one).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_book.py tests/test_mm_book.py
git commit -m "feat(complete): best_ask helper (min ask, CLOB order-safe)"
```

---

### Task 2: `completion_buy` decision (pure)

**Files:**
- Create: `quoter/research/mm_complete.py`
- Test: `tests/test_mm_complete.py`

**Interface:** `completion_buy(heavy_side, heavy_avg, light_ask, naked_qty, threshold=1.0) -> (light_side, qty, price) | None`. If we hold `naked_qty` shares naked on `heavy_side` (cost-avg `heavy_avg`) and buying the opposite ("light") side at `light_ask` keeps the pair cost `heavy_avg + light_ask < threshold`, return the taker buy `(light_side, naked_qty, light_ask)`; else `None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mm_complete.py`:

```python
from quoter.research.mm_complete import completion_buy


def test_completes_when_pair_below_threshold():
    # naked Up avg 0.20; light (Down) ask 0.50 -> pair 0.70 < 1 -> buy 10 Down @0.50
    assert completion_buy("Up", 0.20, 0.50, 10) == ("Down", 10.0, 0.50)


def test_light_side_is_opposite_of_heavy():
    assert completion_buy("Down", 0.20, 0.50, 10)[0] == "Up"


def test_no_completion_when_pair_at_or_above_threshold():
    assert completion_buy("Up", 0.60, 0.45, 10) is None      # 1.05 >= 1
    assert completion_buy("Up", 0.50, 0.50, 10) is None      # exactly 1.00, strict <


def test_no_completion_when_not_naked():
    assert completion_buy("Up", 0.20, 0.50, 0) is None


def test_threshold_parameter_is_respected():
    # pair 0.70; threshold 0.65 -> too expensive -> None
    assert completion_buy("Up", 0.20, 0.50, 10, threshold=0.65) is None
    assert completion_buy("Up", 0.20, 0.50, 10, threshold=0.80) == ("Down", 10.0, 0.50)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mm_complete.py -q`
Expected: FAIL (ModuleNotFoundError: quoter.research.mm_complete).

- [ ] **Step 3: Write the implementation**

`quoter/research/mm_complete.py`:

```python
"""Pure pair-completion decision for the MM study. When our deep maker ladder fills one side
naked, taker-buy the opposite side to form a matched pair — but only while the pair still costs
less than `threshold` (else merging would lock a loss, so we ride the naked residual instead)."""
from __future__ import annotations


def completion_buy(heavy_side: str, heavy_avg: float, light_ask: float,
                   naked_qty: float, threshold: float = 1.0):
    if naked_qty <= 0:
        return None
    if heavy_avg + light_ask >= threshold:
        return None
    light_side = "Down" if heavy_side == "Up" else "Up"
    return (light_side, float(naked_qty), float(light_ask))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mm_complete.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_complete.py tests/test_mm_complete.py
git commit -m "feat(complete): pure taker pair-completion decision (complete iff pair < threshold)"
```

---

### Task 3: Two-mode completion report

**Files:**
- Create: `scripts/_book_edge_complete.py`

Glue (run on collected data; not unit-tested; verified by running). Compares baseline / near-end / continuous.

- [ ] **Step 1: Write the script**

`scripts/_book_edge_complete.py`:

```python
"""Does pair-completion flip our queue-grounded edge positive? On the collected book+tape data,
compare BASELINE (no completion) vs NEAR-END vs CONTINUOUS taker-completion: matched-pair
fraction, edge%, win-rate. Read-only, no trading.
Usage: python3 scripts/_book_edge_complete.py <book_jsonl> [threshold]"""
import sys
import json
import statistics

from quoter.research.mm_book import load_snapshots, queue_fill, best_mid, best_ask
from quoter.research.mm_policy import deep_ladder_quotes
from quoter.research.mm_tape import load_window, subgraph_targets
from quoter.research.mm_calibrate import realized_pnl
from quoter.research.mm_complete import completion_buy

BOOK = sys.argv[1] if len(sys.argv) > 1 else "data/book.jsonl"
THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
ADDR = "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
SIZE, LEVELS, STEP = 5, 15, 0.03
WINDOWS_PER_DAY = 288


def _result(inv, spent, winner):
    matched = min(inv["Up"], inv["Down"])
    total = inv["Up"] + inv["Down"]
    return {"pnl": inv[winner] - spent, "spent": spent, "matched": matched, "total": total}


def simulate(slug):
    w = load_window(slug)
    if not w or not w[0]:
        return None
    tape, winner, _ = w
    snaps = load_snapshots(BOOK, slug)
    if not snaps:
        return None
    place = next((s for s in snaps if s["yes"]["bids"] and s["no"]["bids"]), snaps[0])
    pts = place["ts"]
    pbids = {"Up": place["yes"]["bids"], "Down": place["no"]["bids"]}
    ymid = best_mid(place["yes"]["bids"], place["yes"]["asks"])
    quotes = deep_ladder_quotes(ymid, SIZE, LEVELS, STEP)
    side_tape = {"Up": [t for t in tape if t["oi"] == 0 and t["ts"] >= pts],
                 "Down": [t for t in tape if t["oi"] == 1 and t["ts"] >= pts]}
    close_ts = int(slug.rsplit("-", 1)[1]) + 300

    def maker_fills(upto):
        inv = {"Up": 0.0, "Down": 0.0}; cost = {"Up": 0.0, "Down": 0.0}
        for q in quotes:
            st = [t for t in side_tape[q.side] if t["ts"] < upto]
            f = queue_fill(q.price, q.size, pts, pbids[q.side], st)
            if f > 0:
                inv[q.side] += f; cost[q.side] += f * q.price
        return inv, cost

    def ask_at(snap, side):
        return best_ask(snap["yes"]["asks"] if side == "Up" else snap["no"]["asks"])

    # --- baseline: full-window maker fills, hold ---
    inv, cost = maker_fills(close_ts + 1)
    base = _result(inv, cost["Up"] + cost["Down"], winner)

    # --- near-end: complete accumulated naked at the LAST snapshot's ask ---
    ni = dict(inv); nc = dict(cost)
    heavy = "Up" if ni["Up"] > ni["Down"] else "Down"
    light = "Down" if heavy == "Up" else "Up"
    naked = abs(ni["Up"] - ni["Down"])
    havg = nc[heavy] / ni[heavy] if ni[heavy] > 0 else 0.0
    lask = ask_at(snaps[-1], light)
    if lask is not None:
        buy = completion_buy(heavy, havg, lask, naked, THRESHOLD)
        if buy:
            ni[buy[0]] += buy[1]; nc[buy[0]] += buy[1] * buy[2]
    near = _result(ni, nc["Up"] + nc["Down"], winner)

    # --- continuous: complete as naked accrues, snapshot by snapshot ---
    completed = {"Up": 0.0, "Down": 0.0}; taker_cost = 0.0
    for s in snaps:
        mi, mc = maker_fills(s["ts"] + 1)
        cur = {k: mi[k] + completed[k] for k in ("Up", "Down")}
        h = "Up" if cur["Up"] > cur["Down"] else "Down"
        lt = "Down" if h == "Up" else "Up"
        nk = abs(cur["Up"] - cur["Down"])
        ha = mc[h] / mi[h] if mi[h] > 0 else 0.0
        la = ask_at(s, lt)
        if nk > 0 and la is not None:
            buy = completion_buy(h, ha, la, nk, THRESHOLD)
            if buy:
                completed[buy[0]] += buy[1]; taker_cost += buy[1] * buy[2]
    mi, mc = maker_fills(close_ts + 1)
    ci = {k: mi[k] + completed[k] for k in ("Up", "Down")}
    cont = _result(ci, mc["Up"] + mc["Down"] + taker_cost, winner)

    return {"base": base, "near": near, "cont": cont}


def agg(rows, key):
    rs = [r[key] for r in rows]
    spent = sum(r["spent"] for r in rs)
    pnl = sum(r["pnl"] for r in rs)
    matched = sum(r["matched"] for r in rs)
    total = sum(r["total"] for r in rs)
    wins = sum(1 for r in rs if r["pnl"] > 0)
    edge = 100 * pnl / spent if spent else 0.0
    mfrac = 100 * 2 * matched / total if total else 0.0
    return edge, mfrac, 100 * wins / len(rs) if rs else 0.0, spent, pnl


slugs = set()
with open(BOOK) as f:
    for line in f:
        try:
            slugs.add(json.loads(line)["slug"])
        except Exception:
            continue
print("windows with book data:", len(slugs), " threshold:", THRESHOLD)

rows = [r for r in (simulate(s) for s in sorted(slugs)) if r]
print("windows simulated:", len(rows))
if rows:
    print("\n%-10s %10s %12s %10s" % ("mode", "edge%", "matched%", "win%"))
    for key, name in (("base", "baseline"), ("near", "near-end"), ("cont", "continuous")):
        edge, mfrac, winr, spent, pnl = agg(rows, key)
        dpw = spent / len(rows)
        print("%-10s %+9.2f%% %11.0f%% %9.0f%%   spent $%.0f pnl $%+.0f  $/day@$%.0f=%+.0f" %
              (name, edge, mfrac, winr, spent, pnl, dpw, dpw * (edge / 100) * WINDOWS_PER_DAY))

# competitor ground-truth context
tg = subgraph_targets(ADDR, max_pages=20)
comp = [realized_pnl(t, load_window(t["slug"])[1]) for t in tg if load_window(t["slug"])]
if comp:
    print("\ncompetitor ground-truth pnl over %d windows: $%+.2f" % (len(comp), sum(comp)))

print("\nCAVEATS: taker-completion pays the spread (may not fully lock); light_ask from snapshot")
print("is a proxy; small sample/regime; approach-A queue (no intra-tick refill).")
```

- [ ] **Step 2: Verify it parses**

Run: `.venv/bin/python -c "import ast; ast.parse(open('scripts/_book_edge_complete.py').read()); print('parse ok')"`
Expected: `parse ok`. (A full run needs collected data + network; the coordinator runs it separately on the accumulated `data/book_*.jsonl`.)

- [ ] **Step 3: Commit**

```bash
git add scripts/_book_edge_complete.py
git commit -m "feat(complete): baseline/near-end/continuous completion comparison report"
```

---

### Task 4: Full suite green

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all prior tests + the new best_ask test + 5 mm_complete tests).

- [ ] **Step 2: Commit any fixups if needed** (only if a cross-module inconsistency surfaced).

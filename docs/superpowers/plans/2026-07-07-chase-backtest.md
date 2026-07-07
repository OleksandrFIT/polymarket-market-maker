# Momentum-Chase Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline backtest that measures whether a causal momentum *chase* of the winning side improves on the passive top-of-book maker baseline (measured +0.68% edge), by flipping the end-of-window residual from loser (adverse) to winner.

**Architecture:** A pure, unit-tested `chase_signal` module + a read-only backtest script (`scripts/_chase.py`) that reuses the validated `_top_book.py` fill model, adds the chase layer, and sweeps parameters reporting a new `adverse` metric. No live-bot code is touched.

**Tech Stack:** Python 3 (stdlib only), pytest. Data: `data/book_YYYYMMDD.jsonl` (already on server). Tape/winner via `quoter/research/mm_tape.load_window`.

**Spec:** `docs/superpowers/specs/2026-07-07-chase-backtest-design.md`

**Server run notes (memory-constrained 1.9 GB host):**
- Tape cache on disk: prefix every server run with `POLY_MM_CACHE=/home/ubuntu/cache_poly_mm`.
- Run on SLICES (~70–90 windows / ~10k snapshots), never a full day (OOMs at ~1.4 GB).
- Launch long runs as `systemd-run --unit=... --setenv=POLY_MM_CACHE=...`; poll the output file.
- SSH: key `<ssh-key>.pem`, `ubuntu@<current-IP>` (ask user for current IP).

---

## File Structure

- Create: `quoter/research/chase.py` — pure `chase_signal(...)`.
- Create: `tests/test_chase_signal.py` — unit tests for the signal.
- Create: `scripts/_chase.py` — the backtest (passive vs chase + sweep + adverse metric).
- Reuse (no change): `quoter/research/mm_tape.py` (`load_window`), `scripts/_top_book.py` (reference fill model), `data/book_*.jsonl`.

---

### Task 1: `chase_signal` pure function (TDD)

**Files:**
- Create: `quoter/research/chase.py`
- Test: `tests/test_chase_signal.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chase_signal.py
"""chase_signal: causal momentum favorite over the last lookback_sec. Pure — must
never read future ticks; the backtest feeds it only history seen so far."""
from quoter.research.chase import chase_signal


def test_rising_up_mid_returns_up():
    hist = [(0, 0.50), (20, 0.55), (40, 0.62)]
    assert chase_signal(hist, 40, 40, 0.05) == "Up"


def test_falling_up_mid_returns_down():
    hist = [(0, 0.50), (20, 0.46), (40, 0.42)]
    assert chase_signal(hist, 40, 40, 0.05) == "Down"


def test_flat_below_threshold_returns_none():
    hist = [(0, 0.50), (20, 0.51), (40, 0.52)]
    assert chase_signal(hist, 40, 40, 0.05) is None


def test_threshold_is_inclusive():
    hist = [(0, 0.50), (40, 0.55)]           # exactly +0.05
    assert chase_signal(hist, 40, 40, 0.05) == "Up"


def test_uses_sample_at_or_before_lookback_edge():
    # lookback 40s from now=100 -> edge at ts 60; the 0.50 at ts 60 is the baseline,
    # so 0.60 now is +0.10 -> Up (the older 0.30 at ts 0 must be ignored).
    hist = [(0, 0.30), (60, 0.50), (100, 0.60)]
    assert chase_signal(hist, 100, 40, 0.05) == "Up"


def test_short_history_uses_earliest_sample():
    hist = [(38, 0.50), (40, 0.60)]          # no sample as old as now-40
    assert chase_signal(hist, 40, 40, 0.05) == "Up"


def test_empty_history_returns_none():
    assert chase_signal([], 40, 40, 0.05) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chase_signal.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'quoter.research.chase'`

- [ ] **Step 3: Write the minimal implementation**

```python
# quoter/research/chase.py
"""Causal momentum-chase signal for the MM backtest. Pure + unit-tested.
Given the Up-side mid history seen SO FAR, name the side that has been rising (the
momentum favorite) over the last `lookback_sec`, or None if the move is below
`threshold`. Never reads future ticks — the caller passes only history up to `now_ts`."""
from __future__ import annotations


def chase_signal(mid_hist, now_ts, lookback_sec, threshold):
    """mid_hist: list of (ts, up_mid) in time order, up to and including now.
    Return "Up" if up_mid rose >= threshold over the last lookback_sec, "Down" if it
    fell >= threshold, else None. (up_mid + down_mid == 1, so Up rising == Down falling.)"""
    if not mid_hist:
        return None
    cur = mid_hist[-1][1]
    cutoff = now_ts - lookback_sec
    past = None
    for ts, m in mid_hist:
        if ts <= cutoff:
            past = m            # last sample at/before the lookback edge
        else:
            break
    if past is None:
        past = mid_hist[0][1]   # history shorter than lookback: use earliest sample
    delta = cur - past
    if delta >= threshold:
        return "Up"
    if delta <= -threshold:
        return "Down"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chase_signal.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add quoter/research/chase.py tests/test_chase_signal.py
git commit -m "feat(chase): causal momentum-favorite signal (pure, unit-tested)"
```

---

### Task 2: `scripts/_chase.py` backtest (passive reproduction + chase + sweep)

**Files:**
- Create: `scripts/_chase.py`

**Verification anchor (not a unit test):** the PASSIVE row this script prints must match
`_top_book.py`'s baseline on the same slice (+0.68% edge on the 74-window 2026-07-04 slice).
If it doesn't, the fill-model port is wrong — fix before trusting the chase row.

- [ ] **Step 1: Write the script**

```python
# scripts/_chase.py
"""Momentum-chase backtest: passive top-of-book + causal chase of the winning side.
Reuses _top_book.py's fill model (bid best+tick, fill SELL prints <= our bid, merge,
hold residual). Adds: take the rising side at its ask (chase), and an `adverse` metric
(loser shares held to resolution). Read-only.
Memory: run on SLICES on the 1.9G server; set POLY_MM_CACHE=/home/ubuntu/cache_poly_mm.
Usage: python3 scripts/_chase.py <book_jsonl>"""
import sys
import json
import collections

from quoter.research.mm_tape import load_window
from quoter.research.chase import chase_signal

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_all.jsonl"
TICK = 0.001

byslug = collections.defaultdict(list)
for l in open(BOOK):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    byslug[r["slug"]].append(r)
for s in byslug.values():
    s.sort(key=lambda x: x["ts"])

WINS = {}
for slug in byslug:
    w = load_window(slug)
    if w and w[0]:
        WINS[slug] = w


def _mid(book):
    bb = max((float(p) for p, _ in book["bids"]), default=None)
    ba = min((float(p) for p, _ in book["asks"]), default=None)
    if bb is None and ba is None:
        return None
    if bb is None:
        return ba
    if ba is None:
        return bb
    return (bb + ba) / 2


def run(SIZE=5.0, CAP=10.0, theta=1.0, fee=0.0,
        chase=False, lookback=40, threshold=0.05, chase_max=0.85, chase_size=5.0):
    agg = {"pnl": 0., "spent": 0., "n": 0, "win": 0, "merged": 0., "fills": 0., "adverse": 0.}
    for slug, snaps in byslug.items():
        if slug not in WINS:
            continue
        tape, winner, _ = WINS[slug]
        st = {0: [t for t in tape if t["oi"] == 0], 1: [t for t in tape if t["oi"] == 1]}
        inv = {"Up": 0., "Down": 0.}
        spent = 0.
        returned = 0.
        fills = 0.
        merged = 0.
        mid_hist = []
        for i, snap in enumerate(snaps):
            ts = snap["ts"]
            end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
            um = _mid(snap["yes"])
            if um is not None:
                mid_hist.append((ts, um))
            # passive maker fills — identical to _top_book.py
            for side, oi, book in (("Up", 0, snap["yes"]), ("Down", 1, snap["no"])):
                bids = book["bids"]
                asks = book["asks"]
                if not bids:
                    continue
                other = "Down" if side == "Up" else "Up"
                if inv[side] - inv[other] >= CAP:
                    continue
                bb = max(float(p) for p, _ in bids)
                ba = min((float(p) for p, _ in asks), default=1.0)
                our = round(bb + TICK, 3)
                if our >= ba or our >= 0.99:
                    continue
                v = sum(t["size"] for t in st[oi]
                        if ts <= t["ts"] < end and t["side"] == "SELL" and t["price"] <= our)
                f = min(SIZE, v * theta)
                if f > 0:
                    inv[side] += f
                    spent += f * (our + fee)
                    fills += f
            # chase — TAKE the rising (winning-favorite) side at its ask, bounded
            if chase:
                sig = chase_signal(mid_hist, ts, lookback, threshold)
                if sig is not None:
                    other = "Down" if sig == "Up" else "Up"
                    book = snap["yes"] if sig == "Up" else snap["no"]
                    ba = min((float(p) for p, _ in book["asks"]), default=None)
                    if ba is not None and ba <= chase_max and inv[sig] - inv[other] < CAP:
                        f = min(chase_size, CAP - (inv[sig] - inv[other]))
                        if f > 0:
                            inv[sig] += f
                            spent += f * (ba + fee)
                            fills += f
            # merge matched pairs -> $1 each
            m = min(inv["Up"], inv["Down"])
            if m > 0:
                returned += m
                merged += m
                inv["Up"] -= m
                inv["Down"] -= m
        if spent <= 0:
            continue
        returned += inv[winner]                       # residual winner pays $1
        loser = "Down" if winner == "Up" else "Up"
        agg["adverse"] += inv[loser]                  # loser residual = adverse selection
        agg["pnl"] += returned - spent
        agg["spent"] += spent
        agg["n"] += 1
        agg["win"] += (returned - spent > 0)
        agg["merged"] += merged
        agg["fills"] += fills
    return agg


def _fmt(tag, a):
    if a["n"] == 0 or a["spent"] <= 0:
        print("  %-30s no data" % tag)
        return
    print("  %-30s edge %+0.2f%%  win %.0f%%  matched %.0f%%  adverse %.1f sh/win  $/win %+0.3f  n=%d" % (
        tag, 100 * a["pnl"] / a["spent"], 100 * a["win"] / a["n"],
        100 * 2 * a["merged"] / a["fills"] if a["fills"] else 0,
        a["adverse"] / a["n"], a["pnl"] / a["n"], a["n"]))


print("windows: %d (resolved %d)" % (len(byslug), len(WINS)))

print("\n=== PASSIVE vs CHASE (size 5, cap 10, theta 1, fee 0) ===")
_fmt("passive", run())
_fmt("chase(lb40,thr.05,max.85)", run(chase=True))

print("\n=== chase param sweep (fee 0) ===")
for lb in (20, 40, 60):
    for thr in (0.03, 0.05, 0.08):
        for cmax in (0.80, 0.85, 0.90):
            _fmt("chase lb%d thr%.2f max%.2f" % (lb, thr, cmax),
                 run(chase=True, lookback=lb, threshold=thr, chase_max=cmax))

print("\n=== fee gate (edge dies past ~0.5c on passive; check chase too) ===")
for fee in (0.0, 0.002, 0.005):
    _fmt("passive fee%.3f" % fee, run(fee=fee))
    _fmt("chase   fee%.3f" % fee, run(chase=True, fee=fee))
```

- [ ] **Step 2: Verify the script imports and the passive row reproduces the baseline**

Prepare a slice on the server (streaming, low memory) and run:

```bash
# on the server (~/poly-quoter):
head -n 10000 data/book_20260704.jsonl > ~/book_slice.jsonl
POLY_MM_CACHE=/home/ubuntu/cache_poly_mm .venv/bin/python scripts/_chase.py ~/book_slice.jsonl
```

Expected: the `passive` row shows `edge +0.68%` (±0.05) — matching `_top_book.py` on the
same 74-window slice — and a `chase(...)` row prints with its own edge/adverse. If passive
does NOT match, the port is wrong; diff against `_top_book.py`'s `run()` and fix.

- [ ] **Step 3: Commit**

```bash
git add scripts/_chase.py
git commit -m "feat(chase): backtest — passive vs momentum-chase, adverse metric + sweep"
```

---

### Task 3: Run the decision sweep on ≥3 old days and record results

**Files:**
- Modify: `docs/superpowers/specs/2026-07-07-chase-backtest-design.md` (append a Results section)

- [ ] **Step 1: Run the sweep on three separate old days (slices), as background units**

For each of `20260702`, `20260703`, `20260704` (full resolution, distinct regimes):

```bash
# on the server, per day D:
head -n 10000 data/book_${D}.jsonl > ~/slice_${D}.jsonl
sudo systemd-run --unit=chase_${D} --working-directory=/home/ubuntu/poly-quoter \
  --setenv=POLY_MM_CACHE=/home/ubuntu/cache_poly_mm \
  bash -c ".venv/bin/python scripts/_chase.py ~/slice_${D}.jsonl > /tmp/chase_${D}.out 2>&1"
# poll: cat /tmp/chase_${D}.out  (wait for the fee-gate section to appear)
```

- [ ] **Step 2: Apply the decision rule from the spec**

Record, per day: passive edge/adverse vs best chase edge/adverse, and the fee-gate rows.
Chase PASSES only if across all three days:
1. best chase edge > passive edge at fee ≥ 0.002, AND
2. chase adverse < passive adverse, AND
3. no day/segment flips chase materially negative.

- [ ] **Step 3: Write the Results section and commit**

Append a `## Results (2026-07-07)` section to the spec with the per-day table and the
verdict (proceed to Phase-3 live spec, or close as passive-is-best). Then:

```bash
git add docs/superpowers/specs/2026-07-07-chase-backtest-design.md
git commit -m "docs(chase): backtest results + Phase-3 go/no-go verdict"
```

- [ ] **Step 4: Clean up server**

```bash
for D in 20260702 20260703 20260704; do sudo systemctl reset-failed chase_$D 2>/dev/null; done
rm -f ~/slice_*.jsonl /tmp/chase_*.out
```

---

## Notes

- `book_collector` (systemd unit `bookcollect`) keeps collecting fresh data; leave it running.
- The live bot stays STOPPED/dry-run/LOCKED throughout — this plan touches no order path.
- If chase PASSES, the next step is a NEW brainstorm→spec for Phase 3 (live implementation
  of chase in `merge_runner`), not a direct edit here.

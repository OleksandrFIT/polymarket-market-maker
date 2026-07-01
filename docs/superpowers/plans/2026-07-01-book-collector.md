# Order-Book Collector + Queue-Aware Fill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only order-book snapshot collector (AWS, own systemd service) plus a pure queue-aware fill analysis that measures whether our small deep maker bids would actually fill given the real queue ahead of us.

**Architecture:** `book_collector` polls CLOB `/book` every 2s and appends JSONL snapshots; `mm_book` provides pure `depth_ahead`/`queue_fill` over those snapshots + the trade tape; `_book_edge` runs our deep-ladder policy through the measured queue and reports our fill-rate-grounded edge% vs the competitor's. Zero trading — the collector imports no order code and only issues GET.

**Tech Stack:** Python 3.13, stdlib + httpx, pytest. Reuses `quoter/research/` (mm_tape, mm_policy, mm_calibrate) and `quoter.markets`. `.venv/bin/python`. Branch `master`.

**Conventions:** side names `"Up"`/`"Down"`; tape trade record `{"ts","side","oi","price","size"}` (side = taker side); CLOB `/book` returns `{"bids":[{"price":"..","size":".."},...],"asks":[...]}`.

---

### Task 1: Collector pure helpers

**Files:**
- Create: `quoter/research/book_collector.py`
- Test: `tests/test_book_collector.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_book_collector.py`:

```python
from quoter.research.book_collector import current_slug, snapshot_record


def test_current_slug_floors_to_5m_boundary():
    # 1782909130 is inside the window opening at 1782909000 (=5943030*300)
    assert current_slug(1782909130) == "btc-updown-5m-1782909000"
    assert current_slug(1782909000) == "btc-updown-5m-1782909000"


def test_snapshot_record_normalizes_clob_books():
    yb = {"bids": [{"price": "0.52", "size": "100"}], "asks": [{"price": "0.55", "size": "40"}]}
    nb = {"bids": [{"price": "0.46", "size": "80"}], "asks": [{"price": "0.49", "size": "30"}]}
    rec = snapshot_record(1782909130, "btc-updown-5m-1782909000", yb, nb)
    assert rec["ts"] == 1782909130
    assert rec["slug"] == "btc-updown-5m-1782909000"
    assert rec["yes"]["bids"] == [[0.52, 100.0]]
    assert rec["yes"]["asks"] == [[0.55, 40.0]]
    assert rec["no"]["bids"] == [[0.46, 80.0]]


def test_snapshot_record_handles_missing_sides():
    rec = snapshot_record(1, "s", {}, {"bids": [{"price": "0.4", "size": "5"}]})
    assert rec["yes"]["bids"] == [] and rec["yes"]["asks"] == []
    assert rec["no"]["bids"] == [[0.4, 5.0]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_book_collector.py -q`
Expected: FAIL (ModuleNotFoundError: quoter.research.book_collector).

- [ ] **Step 3: Write the pure helpers**

`quoter/research/book_collector.py`:

```python
"""Read-only order-book snapshot collector for the MM queue-fill study. Polls CLOB /book
every 2s for the current BTC 5m window and appends JSONL snapshots. Imports NO order-
placement code and only issues GET requests. Pure helpers (current_slug, snapshot_record)
are unit-tested; the loop is thin I/O verified by running with --once."""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

WINDOW_SEC = 300
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
UA = {"User-Agent": "Mozilla/5.0 (poly-book)"}


def current_slug(now: float) -> str:
    open_ts = int(now) // WINDOW_SEC * WINDOW_SEC
    return "btc-updown-5m-%d" % open_ts


def _side(book) -> dict:
    b = book or {}
    return {
        "bids": [[float(l["price"]), float(l["size"])] for l in b.get("bids", [])],
        "asks": [[float(l["price"]), float(l["size"])] for l in b.get("asks", [])],
    }


def snapshot_record(ts, slug, yes_book, no_book) -> dict:
    return {"ts": int(ts), "slug": slug, "yes": _side(yes_book), "no": _side(no_book)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_book_collector.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/book_collector.py tests/test_book_collector.py
git commit -m "feat(book): collector pure helpers (current_slug, snapshot_record)"
```

---

### Task 2: Collector I/O loop

**Files:**
- Modify: `quoter/research/book_collector.py` (append I/O + main)

This is thin I/O (not unit-tested); verified by a `--once` smoke run.

- [ ] **Step 1: Append the I/O loop**

Add to `quoter/research/book_collector.py`:

```python
_token_cache: dict[str, tuple[str, str]] = {}


def _resolve_tokens(client: httpx.Client, slug: str):
    """Return (yes_token, no_token) for a slug via gamma, cached. None on failure."""
    if slug in _token_cache:
        return _token_cache[slug]
    try:
        r = client.get("%s/markets" % GAMMA, params={"slug": slug}, headers=UA, timeout=10)
        arr = r.json()
        toks = json.loads(arr[0]["clobTokenIds"]) if arr else None
        if toks and len(toks) == 2:
            _token_cache[slug] = (toks[0], toks[1])
            return _token_cache[slug]
    except Exception:
        pass
    return None


def _get_book(client: httpx.Client, token_id: str) -> dict:
    r = client.get("%s/book" % CLOB, params={"token_id": token_id}, headers=UA, timeout=10)
    return r.json()


def _tick(client: httpx.Client) -> dict | None:
    """One collection tick: resolve tokens, fetch both books, return the JSONL record or None."""
    now = time.time()
    slug = current_slug(now)
    toks = _resolve_tokens(client, slug)
    if not toks:
        return None
    yb = _get_book(client, toks[0])
    nb = _get_book(client, toks[1])
    return snapshot_record(now, slug, yb, nb)


def _append(rec: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    day = time.strftime("%Y%m%d", time.gmtime(rec["ts"]))
    with open(os.path.join(DATA_DIR, "book_%s.jsonl" % day), "a") as f:
        f.write(json.dumps(rec) + "\n")


def main() -> None:
    once = "--once" in sys.argv
    with httpx.Client(http2=False) as client:
        while True:
            try:
                rec = _tick(client)
                if rec is not None:
                    _append(rec)
                    if once:
                        print("wrote snapshot for", rec["slug"],
                              "yes_bids", len(rec["yes"]["bids"]),
                              "no_bids", len(rec["no"]["bids"]))
            except Exception as e:  # never crash the service
                if once:
                    print("tick error:", e)
            if once:
                return
            time.sleep(2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test locally (best-effort — needs network)**

Run: `.venv/bin/python -m quoter.research.book_collector --once`
Expected: either `wrote snapshot for btc-updown-5m-<ts> yes_bids <n> no_bids <n>` and a line appended to `data/book_<today>.jsonl`, OR `tick error: ...` if the network/geoblock prevents it locally (the real run is on AWS). Either way it must exit cleanly (no traceback).

- [ ] **Step 3: Confirm existing tests still pass**

Run: `.venv/bin/python -m pytest tests/test_book_collector.py -q`
Expected: PASS (3 passed).

- [ ] **Step 4: Commit**

```bash
git add quoter/research/book_collector.py
git commit -m "feat(book): read-only collector loop (gamma token resolve + CLOB /book poll)"
```

---

### Task 3: Queue-aware fill (pure)

**Files:**
- Create: `quoter/research/mm_book.py`
- Test: `tests/test_mm_book.py`

**Interfaces:**
- `depth_ahead(bid_levels, price) -> float`: sum of size at bid levels with `level_price >= price`.
- `queue_fill(price, size, placed_ts, bid_levels, tape) -> float`: `ahead = depth_ahead(...)`; accumulate same-side taker-SELL volume in `tape` (records `{"ts","side","price","size"}`) with `ts >= placed_ts` and `price <= price` into `consumed`; return `min(size, max(0.0, consumed - ahead))`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mm_book.py`:

```python
from quoter.research.mm_book import depth_ahead, queue_fill


def test_depth_ahead_sums_levels_at_or_above_price():
    bids = [[0.60, 10], [0.50, 20], [0.40, 5]]
    assert depth_ahead(bids, 0.50) == 30.0     # 0.60 and 0.50
    assert depth_ahead(bids, 0.55) == 10.0     # only 0.60
    assert depth_ahead(bids, 0.40) == 35.0
    assert depth_ahead([], 0.50) == 0.0


def S(ts, side, price, size):
    return {"ts": ts, "side": side, "price": price, "size": size}


def test_queue_not_breached_no_fill():
    # ahead=30, only 20 sold through our price -> 0
    tape = [S(5, "SELL", 0.49, 20)]
    assert queue_fill(0.50, 100, 0, [[0.50, 30]], tape) == 0.0


def test_queue_partially_breached_partial_fill():
    # ahead=30, 50 sold -> 20 reaches us, size 100 -> 20
    tape = [S(5, "SELL", 0.49, 50)]
    assert queue_fill(0.50, 100, 0, [[0.50, 30]], tape) == 20.0


def test_our_size_caps_fill():
    tape = [S(5, "SELL", 0.49, 100)]
    assert queue_fill(0.50, 5, 0, [], tape) == 5.0     # ahead 0, plenty sold, capped at 5


def test_taker_buy_ignored():
    tape = [S(5, "BUY", 0.49, 100)]
    assert queue_fill(0.50, 10, 0, [], tape) == 0.0


def test_trade_above_our_price_ignored():
    tape = [S(5, "SELL", 0.60, 100)]      # 0.60 > our 0.50 bid -> doesn't reach us
    assert queue_fill(0.50, 10, 0, [], tape) == 0.0


def test_trades_before_placement_ignored():
    tape = [S(1, "SELL", 0.49, 100)]      # before placed_ts=5
    assert queue_fill(0.50, 10, 5, [], tape) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mm_book.py -q`
Expected: FAIL (ModuleNotFoundError: quoter.research.mm_book).

- [ ] **Step 3: Write the implementation**

`quoter/research/mm_book.py`:

```python
"""Queue-aware maker-fill measurement from collected order-book snapshots + the trade tape.
Pure `depth_ahead`/`queue_fill`; `load_snapshots` is thin I/O."""
from __future__ import annotations

import json


def depth_ahead(bid_levels: list, price: float) -> float:
    return sum(float(sz) for p, sz in bid_levels if float(p) >= price)


def queue_fill(price: float, size: float, placed_ts: float,
               bid_levels: list, tape: list) -> float:
    ahead = depth_ahead(bid_levels, price)
    consumed = 0.0
    for t in tape:
        if t["ts"] < placed_ts:
            continue
        if t["side"] != "SELL":
            continue
        if t["price"] > price:
            continue
        consumed += float(t["size"])
    return min(float(size), max(0.0, consumed - ahead))


def load_snapshots(path: str, slug: str) -> list:
    """Read a book_YYYYMMDD.jsonl file, return snapshots for one slug, sorted by ts."""
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("slug") == slug:
                out.append(rec)
    out.sort(key=lambda r: r["ts"])
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_mm_book.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/research/mm_book.py tests/test_mm_book.py
git commit -m "feat(book): pure queue-aware fill (depth_ahead + queue_fill) + snapshot loader"
```

---

### Task 4: Edge report script

**Files:**
- Create: `scripts/_book_edge.py`

Glue (run manually, network + local snapshot files). Not unit-tested; verified by running.

- [ ] **Step 1: Write the script**

`scripts/_book_edge.py`:

```python
"""Queue-grounded go/no-go: run OUR small deep-ladder policy through the REAL measured queue
(collected book snapshots) + real tape, per resolved window, and report our fill-rate-grounded
edge% vs the competitor's ground-truth edge%. Read-only.
Usage: python3 scripts/_book_edge.py <book_jsonl_path> [addr]"""
import sys
import statistics
import collections

from quoter.research.mm_book import load_snapshots, queue_fill
from quoter.research.mm_policy import deep_ladder_quotes
from quoter.research.mm_tape import load_window, competitor_targets, subgraph_targets
from quoter.research.mm_calibrate import realized_pnl

BOOK = sys.argv[1] if len(sys.argv) > 1 else "data/book.jsonl"
ADDR = sys.argv[2] if len(sys.argv) > 2 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
OUR_SIZE = 5          # shares per bid (our scale)
LEVELS = 15
STEP = 0.03
WINDOWS_PER_DAY = 288

# distinct slugs present in the collected book file
import json
slugs = set()
with open(BOOK) as f:
    for line in f:
        try:
            slugs.add(json.loads(line)["slug"])
        except Exception:
            continue
print("windows with collected book data:", len(slugs))

rows = []   # (slug, our_pnl, our_spent)
for slug in sorted(slugs):
    w = load_window(slug)
    if not w or not w[0]:
        continue
    tape, winner, open_ts = w
    snaps = load_snapshots(BOOK, slug)
    if not snaps:
        continue
    inv = {"Up": 0.0, "Down": 0.0}; cost = {"Up": 0.0, "Down": 0.0}
    for snap in snaps:                       # each snapshot = one quote-refresh tick
        ts = snap["ts"]; ybids = snap["yes"]["bids"]; nbids = snap["no"]["bids"]
        ymid = ybids[0][0] if ybids else 0.5   # best bid as mid proxy
        for q in deep_ladder_quotes(ymid, OUR_SIZE, LEVELS, STEP):
            bids = ybids if q.side == "Up" else nbids
            oi = 0 if q.side == "Up" else 1
            side_tape = [{"ts": t["ts"], "side": t["side"], "price": t["price"], "size": t["size"]}
                         for t in tape if t["oi"] == oi and t["ts"] >= ts]
            f = queue_fill(q.price, q.size, ts, bids, side_tape)
            if f > 0:
                inv[q.side] += f; cost[q.side] += f * q.price
    spent = cost["Up"] + cost["Down"]
    if spent <= 0:
        continue
    m = min(inv["Up"], inv["Down"]); returned = m + (inv[winner] - m)
    pnl = returned - spent
    rows.append((slug, pnl, spent))

print("windows simulated with real queue:", len(rows))
if rows:
    tot_pnl = sum(r[1] for r in rows); tot_spent = sum(r[2] for r in rows)
    edge = 100 * tot_pnl / tot_spent
    print("OUR queue-grounded edge%%: total %+.2f%%  spent $%.2f  pnl $%+.2f" % (edge, tot_spent, tot_pnl))
    print("  $/day @ our deployed capital $%.0f/window: $%.2f" %
          (tot_spent / len(rows), (tot_spent / len(rows)) * (edge / 100) * WINDOWS_PER_DAY))

# competitor ground-truth for the same/nearby windows (context)
tg = subgraph_targets(ADDR, max_pages=20)
comp = []
for t in tg:
    w = load_window(t["slug"])
    if w:
        comp.append(realized_pnl(t, w[1]))
if comp:
    cs = sum(realized_pnl(t, load_window(t["slug"])[1]) for t in tg if load_window(t["slug"]))
    print("competitor ground-truth pnl over %d windows: $%+.2f" % (len(comp), sum(comp)))

print("\nCAVEATS: fill needs BOTH sides (naked risk if one-sided); snapshot=tick is a proxy;")
print("small sample/regime; queue_fill ignores intra-tick queue refill (approach A).")
```

- [ ] **Step 2: Verify import wiring (no run needed if no data yet)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('scripts/_book_edge.py').read()); print('parse ok')"`
Expected: `parse ok`. (A full run needs collected data from AWS — done in Phase 3, after collection.)

- [ ] **Step 3: Commit**

```bash
git add scripts/_book_edge.py
git commit -m "feat(book): queue-grounded edge report (our deep-ladder through real queue)"
```

---

### Task 5: Systemd unit + deploy (Phase 1)

**Files:**
- Create: `deploy/poly-book.service`

- [ ] **Step 1: Create the unit file**

`deploy/poly-book.service`:

```ini
[Unit]
Description=poly-quoter order-book snapshot collector (READ-ONLY, no trading)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/poly-quoter
ExecStart=/home/ubuntu/poly-quoter/.venv/bin/python -m quoter.research.book_collector
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit the unit file**

```bash
git add deploy/poly-book.service
git commit -m "feat(book): systemd unit for read-only collector service"
```

- [ ] **Step 3: Deploy to AWS and start (operator step — run these commands)**

```bash
KEY="~/Desktop/aws keys/<ssh-key>.pem"; HOST="ubuntu@<SERVER_IP>"
# copy the two new modules + unit
scp -i "$KEY" quoter/research/book_collector.py $HOST:~/poly-quoter/quoter/research/book_collector.py
scp -i "$KEY" quoter/research/mm_book.py        $HOST:~/poly-quoter/quoter/research/mm_book.py
scp -i "$KEY" deploy/poly-book.service          $HOST:/tmp/poly-book.service
# smoke-test one tick on the server (writes one snapshot line), then install the service
ssh -i "$KEY" $HOST 'cd ~/poly-quoter && .venv/bin/python -m quoter.research.book_collector --once'
ssh -i "$KEY" $HOST 'sudo cp /tmp/poly-book.service /etc/systemd/system/poly-book.service && sudo systemctl daemon-reload && sudo systemctl enable --now poly-book.service'
```

Expected: the `--once` prints `wrote snapshot for btc-updown-5m-<ts> ...`; the service installs and starts.

- [ ] **Step 4: Verify collection + that poly-control is untouched**

```bash
KEY="~/Desktop/aws keys/<ssh-key>.pem"; HOST="ubuntu@<SERVER_IP>"
ssh -i "$KEY" $HOST 'systemctl is-active poly-book.service; sleep 6; wc -l ~/poly-quoter/data/book_$(date -u +%Y%m%d).jsonl; systemctl is-active poly-control.service; grep -c dry_run ~/poly-quoter/logs/control.log >/dev/null 2>&1; echo poly-control untouched'
```

Expected: `poly-book.service` = `active`; the JSONL line count grows (>1 after 6s); `poly-control.service` still `active` (its live-lock config unchanged — we only ADDED a service).

---

### Task 6: Full suite green

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all prior tests + 10 new: 3 book_collector + 7 mm_book).

- [ ] **Step 2: Commit any fixups if needed** (only if a cross-module inconsistency surfaced).

---

## Phase 3 (later, after data accumulates — NOT part of this build)

After the collector has run several days: `scp` the `data/book_*.jsonl` files down (or run the
report on the server) and run `.venv/bin/python scripts/_book_edge.py data/book_<concat>.jsonl`
to read the queue-grounded edge%. This is an operator step, not an implementation task.

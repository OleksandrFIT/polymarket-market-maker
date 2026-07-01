# Order-Book Snapshot Collector + Queue-Aware Fill Analysis — Design

**Date:** 2026-07-01
**Status:** approved (brainstorm), pending implementation plan
**Follows:** `2026-07-01-mm-simulator-design.md` (this resolves that tool's one open blocker)

## Goal

Measure — not guess — whether our small deep maker bids would actually get FILLED given the
real order-book queue ahead of us, so the merge-maker edge% (already proven scale-free from
the competitor's real fills) can be projected to our capital with a defensible fill-rate.
This resolves blocker #1 from the MM simulator: "we can reproduce his fill PRICES but
haven't shown we can achieve those fills at our queue position."

## Why

The competitor's realized edge is real and scale-invariant (+1.98% total / +5.22% median /
68% win over 63 windows, from his real subgraph fills + winners). His edge lives in DEEP
CHEAP-TAIL fills caught on panic dumps. Whether OUR small deep bids get filled depends on the
queue ahead of us — invisible in trade-tape-only data. We must collect the live order book
(depth over time) to measure it. Read-only; zero trading.

## Hard constraints

- **Read-only. Zero orders.** The collector imports NO order-placement code — only an HTTP
  client and the market-discovery parser. It only issues `GET /book`.
- Runs as a SEPARATE systemd service (`poly-book.service`) on the AWS ca-central-1 server,
  independent of `poly-control` (whose live-lock stays untouched).

## Architecture

Two phases. Phase 1 (collector) deploys and starts gathering immediately. Phase 2 (analysis)
is pure/testable and built in parallel; the report runs after data accumulates.

```
book_collector (I/O service) → JSONL snapshots → mm_book (loader) → queue_fill (pure)
                                                                   → _book_edge (report)
```

## Components

### 1. `quoter/research/book_collector.py` (I/O service)
Loop every 2s:
- Compute current BTC 5m window: `open_ts = int(now)//300*300`, slug `btc-updown-5m-<open_ts>`.
- Resolve the window's two token ids (reuse `quoter.markets` discovery / the escaped-JSON
  parser; cache per slug).
- `GET https://clob.polymarket.com/book?token_id=<yes>` and `<no>` (full depth).
- Append ONE JSONL line: `{"ts": int, "slug": str, "yes": {"bids": [[price,size],...],
  "asks": [...]}, "no": {"bids": [...], "asks": [...]}}`.
- File per UTC day: `data/book_YYYYMMDD.jsonl` (append). Window rollover needs no special
  logic — the slug/tokens are recomputed each loop. On any HTTP error: skip this tick, keep
  going (best-effort; never crash the service).

Pure helper (unit-tested), separated from the loop:
- `current_slug(now) -> str` — the BTC 5m slug for a given epoch.
- `snapshot_record(ts, slug, yes_book, no_book) -> dict` — build the JSONL record (normalize
  book dicts to `[[price,size],...]` float lists).

### 2. `quoter/research/mm_book.py` (loader + pure queue math)
- `load_snapshots(path, slug) -> list[dict]` (I/O): read the JSONL file, return snapshots for
  one slug, sorted by ts.
- `depth_ahead(bid_levels, price) -> float` (PURE): sum of resting size at bid levels with
  `level_price >= price` (higher bids + existing same-price size have queue priority over our
  new bid at `price`). `bid_levels` = `[[price,size],...]`.
- `queue_fill(price, size, placed_ts, snapshot, tape) -> float` (PURE): measured fill of our
  resting bid at `price`:
  1. `ahead = depth_ahead(snapshot["<side>"]["bids"], price)` (queue ahead at placement).
  2. Walk `tape` trades after `placed_ts`; accumulate taker-SELL volume on our side with trade
     price `<= price` into `consumed`.
  3. Our fill = `min(size, max(0.0, consumed - ahead))`.
  (Side selection: caller passes the correct side's snapshot bids and a same-side tape; keep
  the function side-agnostic — it just needs bid_levels + same-side SELL trades.)

### 3. `scripts/_book_edge.py` (report — the real go/no-go)
For each window that has BOTH collected snapshots AND a resolved winner (via `mm_tape.load_window`):
- Run OUR small deep-ladder policy (`mm_policy.deep_ladder_quotes` at our capital/size) tick by
  tick; fill each bid via `queue_fill` (queue from the snapshot at that tick, flow from the
  tape); merge matched pairs; resolve.
- Output: OUR realized edge% WITH real queue, $/day projection at our capital levels, side by
  side with the competitor's ground-truth edge% (`mm_calibrate.realized_pnl`). The decision:
  does our small-size edge% stay positive once real queue-ahead is charged?
- Honest caveats: sample size/regime; both-sides-fill requirement (naked risk if only one
  side fills); collector coverage gaps.

## Deployment (two-phase)

1. Build + unit-test collector; deploy to AWS (scp + `poly-book.service` systemd unit);
   `systemctl start poly-book` → snapshots accumulate immediately. Verify a few lines land in
   `data/book_YYYYMMDD.jsonl` and that `poly-control`'s live-lock is untouched.
2. Build + unit-test the analysis (pure, synthetic fixtures) in parallel.
3. After several days of data, pull the JSONL and run `_book_edge.py` → the fill-rate-grounded
   edge% and $/day.

## Testing

Pure units, no network (fixtures):
- `current_slug` (epoch → correct 5m slug), `snapshot_record` (dict normalization).
- `depth_ahead` (empty / levels above & below P / exactly at P).
- `queue_fill` (queue not breached → 0; partially breached → partial; our-size cap; taker BUY
  ignored; price-above-bid trades ignored).
The collector loop and `load_snapshots`/`_book_edge` I/O are thin and verified by running, not
unit-tested.

## Out of scope (YAGNI)

- No trading, no order placement, no live-lock changes.
- BTC 5m only (the target market); no 15m/ETH.
- No real-time analysis; batch report after collection.
- No approach-C full per-snapshot queue-advance matching (approach A: queue-ahead at placement
  + tape consumption is sufficient for a fill-rate estimate).

## Success criteria

1. Collector runs read-only on AWS as its own service, writing 2s full-depth snapshots, with
   `poly-control` untouched.
2. `depth_ahead` / `queue_fill` / `current_slug` / `snapshot_record` unit-tested.
3. `_book_edge.py` produces our fill-rate-grounded edge% + $/day vs the competitor's edge%.
4. Zero live-trading surface touched.

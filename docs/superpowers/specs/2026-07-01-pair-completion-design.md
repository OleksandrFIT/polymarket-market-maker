# Pair-Completion Policy + Two-Mode Analysis — Design

**Date:** 2026-07-01
**Status:** approved (brainstorm), pending implementation plan
**Follows:** `2026-07-01-book-collector-design.md` (uses its collected book snapshots + queue-fill)

## Goal

Add pair-completion to our simulated market-maker policy and measure, on the real collected
book+tape data, whether completing naked fills into matched pairs (the competitor's actual
mechanism) raises our matched-pair fraction from ~15% toward the competitor's ~85% and flips
our queue-grounded edge% from negative toward positive. Compare two completion timings
(continuous vs near-end) against the no-completion baseline. Offline, zero trading.

## Why (measured this session)

- Our naive symmetric deep maker ladder, on real book queue data (34 windows), fills ~78% on
  the LOSING side and holds it NAKED (only ~15% matched) → **−40% edge**.
- The competitor's real fills (63 windows) are **83% matched (median 89%)**; his edge is
  spread capture via merged pairs, NOT a favorite tilt (his naked residual is ~coin-flip on
  winner/loser). See memory `project-five-min-findings`.
- The missing lever is therefore **pair completion**: when our deep bid fills one side naked,
  buy the opposite side to form a matched pair < $1 (merge → lock the spread) instead of
  riding a naked loser. Precedent exists in `quoter/runner/flatten_planner.plan_naked_action`
  (complete when `heavy_avg + light_ask < 1`).

## Hard constraints

- **Offline, zero trading.** This is analysis over collected data; nothing touches
  `quoter/runner/`, `run_control`, or places any order. The book collector keeps running
  unchanged (it is strategy-agnostic — no re-collection needed for a policy change).

## Architecture

One new pure module + one analysis extension, reusing `mm_book` (queue_fill, best_mid),
`mm_policy` (deep_ladder_quotes), and `mm_tape` (load_window).

```
mm_complete (pure decision) → _book_edge_complete (baseline vs continuous vs near-end report)
```

## Components

### 1. `quoter/research/mm_complete.py` (pure)

```
completion_buy(heavy_side, heavy_avg, light_ask, naked_qty, threshold=1.0)
    -> (light_side, qty, price) | None
```
- `light_side` = the opposite of `heavy_side` ("Up"↔"Down").
- If `naked_qty > 0` and `heavy_avg + light_ask < threshold`: return
  `(light_side, naked_qty, light_ask)` — a taker buy of the light side at its best ask that
  matches the naked shares into pairs.
- Else return `None` (pair would cost ≥ threshold → do not lock a loss; ride naked).
- Pure, no I/O. The realized lock per completed pair is `threshold - (heavy_avg + light_ask)`
  (bounded by market, informational).

### 2. `scripts/_book_edge_complete.py` (report)

For each window with collected snapshots + a resolved winner (`load_window`):
1. **Maker fills (shared baseline):** place the deep ladder once (persistent bid, as in
   `_book_edge`), fill via `queue_fill` over the full window → per-side inventory `inv`,
   cost, and — for continuous mode — the per-snapshot cumulative fills.
2. **Baseline:** hold `inv` to resolution (no completion). Record edge%, matched%.
3. **Near-end completion:** at the LAST snapshot, if naked on the heavy side, call
   `completion_buy(heavy, heavy_avg, light_best_ask_at_last_snapshot, naked, thr)`; if it
   returns a buy, add those shares+cost (taker at ask). Hold to resolution. Record edge%, matched%.
4. **Continuous completion:** iterate snapshots in time order; track cumulative maker fills up
   to each snapshot; when naked appears, call `completion_buy` with THAT snapshot's light-side
   best ask; execute the buy, mark the completed shares so later snapshots don't re-complete
   the same naked. Hold to resolution. Record edge%, matched%.
5. **Output:** a side-by-side table — baseline / near-end / continuous — of matched-pair
   fraction, edge% (total, mean, median), win-rate, fill split, plus the competitor's
   ground-truth edge% for context. Threshold is a CLI/const parameter (default 1.0); optionally
   sweep a couple of values (1.0, 0.98).
6. Honest caveats: sample size/regime; taker-completion pays the spread (may not fully lock);
   `light_ask` from the snapshot is a proxy for the fill we'd actually get; approach-A queue
   model (no intra-tick refill).

`best_ask(ask_levels)` helper: min ask price (CLOB returns asks descending). Added to
`mm_book` as a pure sibling of `best_mid`, and unit-tested.

## Testing

Pure units, no network:
- `completion_buy`: pair below threshold → returns the buy at light_ask; pair at/above
  threshold → None; `naked_qty == 0` → None; light-side selection ("Up" heavy → "Down" buy and
  vice-versa); threshold boundary (strict `<`).
- `best_ask` (if added to mm_book): min over ask levels, empty → None.
The two analysis loops in `_book_edge_complete.py` are glue, verified by running on collected
data (compare baseline/continuous/near-end numbers for sanity — completion must raise matched%).

## Out of scope (YAGNI)

- No live trading, no collector change (book data is strategy-agnostic; reuse it).
- No maker-completion (structurally cannot reach high match — the winner side never dumps to
  our deep bids; that's why taker-completion is required).
- No inventory-cap / risk-sizing tuning yet — first answer "does completion flip the sign?"

## Success criteria

1. `completion_buy` unit-tested (all branches).
2. `_book_edge_complete.py` reports matched% and edge% for baseline vs continuous vs near-end
   on the collected data, showing whether completion raises matched% toward ~85% and the edge
   toward positive.
3. Zero live-trading surface touched; collector untouched.

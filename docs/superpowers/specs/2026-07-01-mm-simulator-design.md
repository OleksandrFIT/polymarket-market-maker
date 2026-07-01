# Realistic Market-Maker Simulator — Design

**Date:** 2026-07-01
**Status:** approved (brainstorm), pending implementation plan

## Goal

Produce ONE honest go/no-go number: the expected **$/day** a full-book market-maker
strategy (replicating competitor `0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82`) would earn
**at our real capital (~$75-90)** and order size, with **queue position** and **adverse
selection** modeled — computed offline on real trade-tape data, calibrated so the simulator
reproduces the competitor's *actual* fills/edge, with **zero live trading**.

## Why (context from research, 2026-07-01)

- On-chain + positions + lb-api proved `0xb27b` is a profitable HFT full-book **maker**:
  +$764k all-time, +$4.3k/day; ~2500 maker fills/hr; tiny fills (median ~9 shares ≈ $4)
  across the WHOLE price curve (0.01→0.99, centered ~mid 0.43); accumulates into large
  positions; **merges** matched pairs (<$1, e.g. current window Up 955@0.136 + Dn 1804@0.721
  = pair cost 0.858 → ~14¢ lock); holds residual to resolution; never sells.
- Earlier "merge-maker dead / edge thin" conclusions were WRONG — they modeled **taker**
  fills at mid, and `data-api/trades` is **taker-only** (maker fills invisible). See
  memory `project-five-min-findings`.
- The maker edge = spread capture × volume. Its viability at OUR scale is UNKNOWN. This
  simulator answers that BEFORE any live engine is built.

## Hard constraint

**No live trading.** This is an offline research tool. It does not touch `merge_runner`,
`run_control`, or place any order. The existing live-lock (`CFG.dry_run=True` + assert)
stays untouched.

## Architecture

Pure, independently-testable units (repo convention: pure planners + unit tests), plus a
thin I/O data layer and a report script. Data flow:

```
mm_tape (I/O) → mm_fill (pure) → mm_sim (pure) → mm_calibrate → _mm_pnl (report)
```

## Components

### 1. `quoter/research/mm_tape.py` (thin I/O)
Fetch + disk-cache per-window data:
- `data-api/trades?market=<conditionId>` → full taker trade tape (list of
  `{timestamp, side BUY/SELL (taker), outcomeIndex 0=Up/1=Dn, price, size}`), paginated,
  capped at 3500 (sufficient per 5m window).
- `gamma-api/markets?slug=<slug>` → conditionId, clobTokenIds, resolution (outcomePrices).
- Competitor ground-truth: `data-api/positions?user=<addr>` (avgPrice, size, outcome,
  slug, asset, mergeable) and `lb-api/profit?window=1d|all&address=<addr>`.
Cache to `/tmp/poly_mm_cache`. Reuses the working access patterns already validated this
session. Network only here; everything downstream is pure and offline.

### 2. `quoter/research/mm_fill.py` (pure — core realism)
`fill(quote, tape_slice, theta) -> FillResult` where a resting quote `(side, price P,
size S, placed_ts)` is filled piecemeal as the tape crosses:
- For each taker trade on our side with price crossing P (a taker SELL at ≤ P for a bid),
  we are at the **back of the queue**: capture `min(S_remaining, V * theta.fill)` where
  `theta.fill` ∈ (0,1] encodes queue depth ahead of us.
- `theta.lag` (seconds): our cancel lags, so during an adverse move after we'd have wanted
  to pull, we still get hit — models latency-driven adverse fills.
- Returns `filled_qty`, `avg_price`, and an `adverse` marker (filled while price moved
  against us). Adverse selection is emergent (fills happen when someone sells into our bid,
  i.e. that side is weakening) — no separate hand-tuned penalty needed.
Pure, no I/O. Unit-tested with hand-crafted tapes.

### 3. `quoter/research/mm_policy.py` (pure)
`quotes(state) -> list[Quote]` given window state (time_left, mid, inventory per side).
Two policies:
- `guru_like`: many small bids on BOTH sides across a wide band of the curve (mimics the
  observed full-book quoting), used for calibration.
- `ours`: capped version — bounded capital, per-order size, N price levels, refresh
  cadence, inventory-skew — the policy we'd actually run. Parameterized so `_mm_pnl` can
  sweep it.

### 4. `quoter/research/mm_sim.py` (pure)
`simulate_window(tape, resolution, policy, theta) -> WindowResult`:
- Replay the window tick-by-tick: get `policy.quotes(state)`, apply `mm_fill` to the tape
  slice since last tick, **merge** matched Up+Down pairs (lock `1 - (up_avg + dn_avg)` per
  matched pair), update inventory/cost.
- At resolution: `pnl = merge_locked + inv[winner] - residual_cost`.
- Returns per-window: filled size & avg price per side, pair cost, merges, pnl, adverse qty.
Pure. Unit-tested with a tiny synthetic window whose PnL is hand-computable.

### 5. `quoter/research/mm_calibrate.py`
Grid-search `theta = (fill, lag)` to make the sim reproduce the competitor's REALITY:
- Calibration set = competitor's current OPEN both-sided windows (pre-merge → gross fills
  visible via positions: avg price + size per side, pair cost) plus recent windows whose
  tapes are fetchable. Post-merge/resolved windows are excluded from the primary anchor
  (their matched legs are merged away). April Goldsky `orderFilledEvents` (maker=addr) is a
  SECONDARY cross-check only.
- For each `theta`: run `mm_sim` with `guru_like` policy on each calibration window's real
  tape; loss = weighted error vs the competitor's real end-state
  (size_up, size_dn, avg_up, avg_dn, pair_cost).
- Pick `theta*` = argmin loss. Cross-validate the calibrated sim's aggregate edge against
  lb-api ($/day this month / today).
- **Honesty gate:** if best-fit loss exceeds a threshold (sim can't reproduce the
  competitor within tolerance), the report must declare the projection UNRELIABLE rather
  than emit a number.

### 6. `scripts/_mm_pnl.py` (report — the deliverable)
Run the calibrated sim (`theta*`) with the `ours` policy at our params over a large recent
window set. Output:
- **Expected $/day at our capital (~$75-90)** — the go/no-go number.
- Breakdown: spread-capture (merges) / residual-resolution / adverse losses.
- Variance / Sharpe.
- **Scaling curve:** $/day vs capital and vs order size (does the edge only exist at the
  competitor's scale?).
- Explicit caveats: `theta*`, calibration fit quality, calibration set size, data period.

## Testing

Pure units, no network (tape fixtures):
- `mm_fill`: crafted tapes — cross / no-cross / partial / queue haircut / lag adverse.
- `mm_policy`: quote generation (band coverage, per-order size, inventory-skew).
- `mm_sim`: synthetic window with known outcome → known PnL; separate merge-logic test.
- `mm_calibrate`: **recovery test** — synthetic "competitor" generated with a known theta;
  calibrator must recover it. Proves the calibrator works before trusting real calibration.

## Out of scope (YAGNI)

- No live engine, no continuous quoter, no order placement.
- No live book-snapshot collector (tape-only fidelity chosen; can be a later refinement if
  the number is promising).
- No multi-asset; BTC 5m first (extendable).

## Success criteria

1. Calibrated sim reproduces the competitor's real per-window end-state within tolerance
   (else honesty gate fires).
2. `_mm_pnl.py` emits a defensible expected $/day at our capital + scaling curve.
3. All pure units unit-tested; calibrator passes the recovery test.
4. Zero live-trading surface touched.

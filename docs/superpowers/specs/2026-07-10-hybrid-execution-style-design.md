# Hybrid Execution Style (0xb27b replica) — Design Spec

**Goal:** Add a THIRD execution style — `hybrid_window` — to the offline execution-A/B comparator,
faithfully replicating 0xb27b's measured ~53% taker / 47% maker execution, and measure the ONE
decisive quantity: does the naked-residual WIN-RATE approach ~50% (a fair coin → zero-mean naked,
like his 55%), or does it stay a biased loser (<30%, adverse-selected) like our maker (19%) and
late-chase momentum (23%)?

**Status:** New pure function in `quoter/research/exec_ab.py` + a third row in the comparator.
NO production-code change, NO live. Research measurement on the same recorded tape.

---

## Background — the decoded mechanism (why hybrid, not pure taker)

0xb27b is NOT a pure taker. Measured: ~53% taker / 47% maker; he earns MAKER_REBATE $4,623 vs
TAKER_REBATE $1,017 (he provides liquidity as a maker). His pair cost ~$0.98 is only achievable
as a hybrid: **cheap LOSER leg (maker bid catches the falling side ~$0.10) + WINNER leg (taker
chases the rising side ~$0.88), matched → 0.10+0.88 = 0.98.** A pure taker pays the spread on both
legs → pair > $1 (exactly our `momentum` style, 0.97–1.04, measured −EV).

The difference from our two failed styles is the naked-residual DIRECTION:
- `top_book` (pure maker): passive bid fills the faller → net-long the LOSER → 19% win-rate.
- `momentum` (late discrete taker chase): fires ~30s after a 3¢ move → buys the mover at the top,
  before a reversal (whipsaw) → net-long the LOSER → 23% win-rate.
- 0xb27b (hybrid, continuous from ~t+10s): maker catches the cheap loser AND taker averages into
  the rising winner from EARLY (low cost basis) → residual leans WINNER → 55% win-rate (fair coin,
  zero-mean naked). The key is EARLY CONTINUOUS accumulation of the winner, not a late one-shot.

## The question this answers (and does NOT answer)

**Answers:** can OUR execution, done identically to his (hybrid, early-continuous), produce a
fair-coin naked (win-rate ~50%) at our size/speed? This is the missing link — the naked-DIRECTION
question, isolated from everything else.

**Does NOT answer:** whether it is profitable. A zero-mean naked contributes ~$0 by itself; profit
still requires pairs < $1 (the arb), which for the maker legs depends on real fills (the separate
live measurement). So the headline metric here is the naked WIN-RATE, not PnL.

## Non-goals (YAGNI)

- No production strategy change. No live. No new config.
- Do NOT optimize for PnL here — the metric is naked win-rate (fair coin vs biased loser).
- Reuse the existing comparator harness, `window_record`, `chase_signal`, `_ask`/`_mid`, shadow-fill.

## Architecture

New pure `hybrid_window(snaps, tape, winner, slug, ...)` in `quoter/research/exec_ab.py`, same
signature/record shape as `top_book_window`/`momentum_window`. Added as a third row in
`scripts/_exec_ab.py` and `scripts/_exec_ab_detail.py`.

### `hybrid_window` model (his execution, faithfully)

Per snapshot, from the open (no late gate):
1. **MAKER leg (cheap loser):** rest best_bid+tick on BOTH sides; shadow-fill = credit
   `min(clip, Σ tape SELL-print size ≤ our bid in [ts, next_ts))` (reuse the shadow-fill rule).
   This catches the falling side cheap.
2. **TAKER leg (chase the winner, EARLY + CONTINUOUS):** compute the causal momentum side via
   `chase_signal(mid_hist, ts, lookback, threshold)`; if a side is rising, TAKE a small clip of it
   at its ask EVERY tick it keeps rising (continuous averaging-in from early, low cost basis) —
   NOT a one-shot late chase. Bounded by `resid_cap` and `per_window_cap` (incl. `taker_fee`).
3. **MERGE** matched pairs each tick (continuous recycle).
4. **NEVER sell.** Residual (leans winner) rides to resolution.
- The maker/taker split emerges from the flow (≈ his 53/47); we do not force a ratio.

### Record & metric

Returns the standard `window_record` (style="hybrid"). The comparator/detail script already report
`resid_outcome` (WON/LOST/flat) → the **naked win-rate** = WON / (WON+LOST) is the headline, printed
alongside top_book and momentum for direct contrast, plus pair_cost/match:naked/PnL for context.

## Decision criterion (naked win-rate)

- **hybrid naked win-rate ≈ 45–55%** → we CAN reproduce his fair-coin (zero-mean) naked at our
  size. The naked-direction problem is SOLVED by execution; the remaining barrier is purely pairs
  < $1 / scale (the live maker-fill question).
- **hybrid naked win-rate still < ~35%** → even his exact execution is adverse-selected at our
  size/speed (our chase can't average into the winner early enough / our fills are still the
  faller). Then his zero-mean is NOT reproducible for us → the replication branch is closed
  definitively, on data.

## Testing

- `tests/test_exec_ab.py`: add a `hybrid_window` unit test on a synthetic rising-Up tape — assert
  it accumulates the rising side (takes Up), merges, never sells, and returns a valid record
  (fields present, pnl consistent). Mechanics only (win-rate edge is the tape-run measurement).
- Full suite stays green.

## Deliverables

- `hybrid_window` in `quoter/research/exec_ab.py` + unit test.
- Third row in `scripts/_exec_ab.py` and `scripts/_exec_ab_detail.py`.
- No production change. Run on the server tape slice → naked win-rate verdict for all three styles.

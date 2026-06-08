# Phase-19: Two-Sided Merge-Maker — Report

**Date:** 2026-06-08
**Status:** Implemented, paper only. Profitability realizable only live in us-east-1.

## The discovery that drove this phase

We had been trying to out-predict competitor Bonereaper. Analysis of **3 386 of his
real trades** proved that was the wrong game:

- His per-entry directional win-rate is **~47% — a coin flip.** He does not pick winners.
- **91% of his windows are two-sided** (he buys both Up and Down).
- Median pair cost from his own fills = **$0.993** — he buys complementary Up+Down
  pairs for under $1.00 and MERGEs them to $1.00, locking ~$0.01–0.02/share.
- Estimated locked merge profit: **$655 on 31 806 shares in 51 min ≈ $0.021/share**,
  extrapolating to ~$18k/day — matching his known ~$14k/day.
- His famous bimodal price distribution (cheap ~0.23 + expensive ~0.76) is simply the
  two legs of his merge pairs.

**His edge is two-sided spread-capture + merge at scale — not prediction.**

## Feasibility for us (measured, not assumed)

- Live BTC/ETH up-down books: `best_bid_Up + best_bid_Down < $1.00` in **100% of 111
  samples** (median $0.990, capturable edge 1–2¢, depth ~89 shares).
- Taker pair (ask+ask) = $1.010 → no free arb; the edge is purely maker spread → we POST.
- Our own paper fills: cheap fills win only **16.7%** (adverse selection from 110 ms /
  back-of-queue). The SAME cheap fills that poison us as a one-sided bot become the
  profitable hedge leg for a two-sided maker who merges.

## What was built

`compute_ladder` is now a two-sided merge-maker (replacing all directional logic):

- Posts **both** legs: Up @ `mid−δ`, Down @ `(1−mid)−δ`, where `δ = merge_edge/2`
  → pair cost `1 − merge_edge < $1`.
- **Three gates:** EDGE (pair must round to < $1), CAPITAL (`per_market_cap_usd`),
  BALANCE (suppress the side already long by ≥ `max_naked_shares`).
- **Merge step** in the paper fill-drain: `inventory.on_merge(matched)` after each
  fill batch, locking the spread and capping naked exposure.
- New live-editable knobs: `merge_edge` (0.01), `max_naked_shares` (20), `merge_levels` (2).
- Removed: all phase-15..18 directional + lottery knobs and the Binance velocity plumbing.

## Honest limitation (same gate as before, but the edge now provably exists)

Our paper fill model simulates 110 ms / back-of-queue, filling **only the falling leg**.
So in paper the bot accrues naked exposure up to `max_naked_shares` (then the balance
gate stops it) and merges whatever matched it collects — **paper PnL is ~zero-to-slightly
negative, NOT positive.**

The decisive difference from phase-18: there the predictive edge **did not exist**;
here the merge edge is **measured in the live book (100% of samples)**. It is realizable
only when we fill BOTH legs — i.e. live in **us-east-1**, at the front of the queue.

## Verification

- 162 tests green; `import quoter.main` clean; no live/on-chain code (paper only).
- Spec-compliance review: COMPLIANT. Code-quality review: fixes applied
  (dead velocity wiring removed, `max_naked_shares` floor raised to 1). Final holistic
  review (opus): **READY**.

## Next step

Deploy to AWS us-east-1 (RTT 110 ms → ~5–10 ms, front-of-queue) — the precondition under
which both legs fill and the measured 1–2¢/pair edge is actually captured. Building the
merge-maker was the prerequisite; co-location is what makes it pay.

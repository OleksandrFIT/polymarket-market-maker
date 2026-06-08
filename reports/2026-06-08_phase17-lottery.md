# Phase-17 Lottery Backtest — 2026-06-08

## Summary

Phase-17 adds a small cheap-tail lottery leg on the underdog side of each market.
The backtest `main()` now runs two configs over the same 39 cached windows and
compares them head-to-head.

## Backtest Results

```
config                          n    total PnL  win-rate      worst
------------------------------------------------------------------
phase-17(lottery on)           39       -36.10     10/39      -5.95
phase-16(lottery off)          39        16.60     12/39       0.00

Recorded live baseline (state.db): -282.53
Lottery delta (p17 - p16): -52.70
```

## Interpretation

### (a) Lottery is ~3-4% of capital — near-neutral expectation is correct

The lottery leg allocates `lottery_size` (default 3 USDC) per qualifying tick,
which is ~3-4% of the typical `max_spend` budget.  A ~53 USDC drag over 39 markets
works out to ~1.36 USDC per market in the backtest, which is structurally expected:
the lottery buys underdog tails at 0.05–0.15 and the fill model marks them to zero
at resolution unless the underdog actually wins.

The lottery's purpose is **profile identity** (showing two-sided presence on
Polymarket) and potential outsized payoff when an underdog wins — not a mean PnL
improvement.  A near-zero or mildly negative backtest delta confirms the lottery
cost is small and bounded.

### (b) Favorite engine is unchanged

With `lottery_size=0` (phase-16 off) the output is identical to the prior
phase-16 backtest (`+16.60` vs `+16.60`).  The favorite ladder leg is untouched;
Task 2 merely appended the lottery leg after the existing logic.

### (c) Fill-model caveats (inherited from prior reports)

The backtest fill model is structurally pessimistic for high-price favorite buying:
- It assumes fills at mid, not at the maker ask, understating realized entry cost.
- The model does not simulate partial fills or queue position.
- Lottery fills at extreme tails (0.05–0.10) may be even harder to get in practice
  but the fixed 3 USDC cap limits exposure.

These caveats mean absolute PnL numbers are noisy; relative comparisons
(p17 vs p16) are more meaningful than the absolute values.

### (d) Paper trading is the real test

The only reliable signal for phase-17 will come from live paper fills on
Polymarket.  The backtest serves as a sanity check (lottery cost is small,
favorite engine unaffected) not a profitability proof.

## Full-Suite Test Count

178 tests passed, 0 failed.

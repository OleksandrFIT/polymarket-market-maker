# Phase-16 Backtest Report — 2026-06-07

## What changed in Task 3

**Part A (bug fix):** `quoter/backtest/engine.py` — `run_market` now passes
`inventory_yes_qty=int(yes_qty), inventory_no_qty=int(no_qty)` into every
`compute_ladder` call. Previously the engine tracked inventory locally but
ignored it in the quote call, so the commit-to-one-side guard in
`compute_ladder` was a no-op in the backtest. This is now wired correctly.

**Part B (cosmetic):** `quoter/strategy/ladder.py` — reduced triple blank line
between `_certainty` and `_favorite_ladder` to the standard two (PEP 8).

**Part C:** `tests/test_backtest_engine.py` — scenario updated to use prices
firmly inside the >=0.85 band (0.86→0.90→0.95→0.99) during the last 40% of
the window, matching phase-16 config defaults exactly.

**Part D:** `quoter/backtest/run_backtest.py` — `sweep()` replaced with a 3×3
grid over `favorite_min_price ∈ {0.80, 0.85, 0.90}` × `entry_start_frac ∈
{0.50, 0.60, 0.70}`.

---

## Phase-16 vs Recorded Live Baseline

```
config                          n    total PnL  win-rate      worst
------------------------------------------------------------------
phase-16(commit-one-side)     115      -316.70    26/115     -37.00

Recorded live baseline (state.db): -282.53
Delta vs live baseline: -34.17
```

The phase-16 default config (`fmin=0.85 estart=0.60`) scores −$316.70 on 115
cached windows, which is −$34 below the −$282.53 recorded live baseline. This
means the backtest does **not** show an improvement for the default config on
this sample. However, the sweep reveals that tighter configs (higher `fmin`,
later `estart`) lose substantially less — see below.

---

## 9-Cell sweep grid: fmin × estart

```
config                          n    total PnL  win-rate      worst
------------------------------------------------------------------
fmin=0.9 estart=0.7           115      -133.80     9/115     -28.80
fmin=0.9 estart=0.5           115      -152.70    16/115     -37.00
fmin=0.9 estart=0.6           115      -155.10    15/115     -37.00
fmin=0.85 estart=0.7          115      -200.00    15/115     -28.80
fmin=0.8 estart=0.7           115      -241.00    17/115     -28.80
fmin=0.85 estart=0.6          115      -316.70    26/115     -37.00
fmin=0.85 estart=0.5          115      -321.60    28/115     -44.60
fmin=0.8 estart=0.6           115      -395.90    29/115     -51.60
fmin=0.8 estart=0.5           115      -398.90    31/115     -51.60
```

Best cell: `fmin=0.90 estart=0.70` at −$133.80 (9W/115). The pattern is clear:
both tighter price floor and later entry reduce losses in this backtest.

---

## Caveats

1. **Coarse fill model.** `fillsim` assumes a fill whenever the bid is touched
   by the next price point; it does not model queue depth, taker arrival rate,
   or our 110 ms Slovakia→us-east-1 latency. Live fill rate will be lower,
   especially for the tighter configs where there are fewer touches.

2. **Per-market cap not modelled correctly.** The backtest passes plain
   `int(yes_qty)` but does not account for accumulated USD cost precisely the
   way the live executor does. The cap guard in `compute_ladder` fires, but the
   exact cutoff timing may differ slightly from live execution.

3. **Small sample, survivorship.** 115 windows from `state.db` are resolved
   markets we already traded. Selection bias is possible: we may have entered
   more markets during high-volatility periods.

4. **Backtest is a filter, not a guarantee.** A config that scores better in
   backtest is worth testing on paper; it is not proof of live edge. The
   recorded live baseline of −$282.53 (8W/37 resolved, 37 markets) is the real
   signal. The next signal is paper P&L under the new config.

5. **Commit-to-one-side is now correctly wired** into the backtest engine as of
   this task (Part A fix). All sweep numbers above reflect the real
   commit-to-one-side logic, not the previously inert version.

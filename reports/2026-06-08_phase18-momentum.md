# Phase-18 Momentum Entry — Design Rationale & Limitations

**Date:** 2026-06-08
**Author:** arix-spec

---

## The cost-basis finding

A head-to-head comparison over 16 shared windows with competitor Bonereaper revealed the root cause of our underperformance: **identical signal, opposite cost basis**.

| Metric | Us (phase ≤ 17) | Bonereaper |
|---|---|---|
| Average entry price | **0.81** | **0.54** |
| Side hit-rate | ~63–69% | ~63–69% |
| EV per share | **−$0.18** | **+$0.15** |

Both bots picked the same side with the same frequency. Our edge was not the problem — our cost basis was. Buying the near-certain favorite at 0.81 means you need an implausibly high win-rate to break even. Bonereaper buys the same side earlier, when it is still priced at ~0.54, and collects $0.15/share expected value from the same market movement.

The arithmetic is stark: at 69% win-rate, buying at 0.54 yields `0.69×(1−0.54) − 0.31×0.54 ≈ +$0.15/share`; buying at 0.81 yields `0.69×(1−0.81) − 0.31×0.81 ≈ −$0.18/share`.

---

## What phase-18 changes

Phase-18 replaces the favorite-buying logic (phase-16) with a **momentum entry** strategy:

- **Signal**: Binance perpetual velocity sign (`velocity_short`) determines which side to buy. Positive velocity → buy YES; negative → buy NO.
- **Price band**: Entry is only allowed inside `[momentum_min_price, momentum_max_price]` (defaults 0.40–0.65). This targets a cost basis around ~0.55, matching Bonereaper's observed entry range.
- **No late-window gate**: Unlike phase-16's 60% window-frac filter, momentum entry can fire at any point in the window as long as the velocity signal is present and the price is cheap.

New Config knobs (live-editable via `/api/settings`):

| Knob | Default | Role |
|---|---|---|
| `momentum_velocity_threshold` | 0.001 | Minimum `\|velocity_short\|` to trigger a buy |
| `momentum_min_price` | 0.40 | Lower bound of cheap band |
| `momentum_max_price` | 0.65 | Upper bound of cheap band |

The lottery leg (phase-17) is unchanged and coexists: it still buys extreme underdogs (price ≤ `lottery_max_price` 0.40) independently.

---

## HARD LIMITATION: phase-18 cannot be evaluated offline

The backtest engine calls the real `compute_ladder` function but passes `velocity_short=None` because **no Binance velocity history is stored in the backtest data files**. The momentum leg checks `if velocity_short is None: return []` and immediately returns no fills.

This is documented by the test `test_momentum_inert_in_backtest_without_velocity`:

```python
def test_momentum_inert_in_backtest_without_velocity():
    # velocity_short=None → momentum leg never fires
    # underdog price 0.45 >= lottery_max_price 0.40 → no lottery either
    series = [PricePoint(120, 0.55), PricePoint(180, 0.55)]
    res = run_market(cfg, _win_market(), series)
    assert res.yes_qty == 0 and res.no_qty == 0
    assert res.pnl == 0.0
```

**Paper trading is the only way to evaluate phase-18.** Any backtest result showing zero fills for the momentum leg is correct behavior, not a bug.

---

## Honest caveat

The core hypothesis — that **Binance velocity predicts the Polymarket winner reliably enough at 0.45–0.60 to be +EV** — is **unproven**.

Phase-18 is built on plausible reasoning:

1. Bonereaper's entries correlate with early momentum (observed from trade data).
2. Binance spot/perp velocity carries information about the underlying asset direction.
3. Polymarket YES/NO prices for crypto binary events tend to converge toward Binance.

But "plausible" is not "validated". The hypothesis will only be confirmed or refuted by a paper run that measures **both**:

- **Realized cost basis** — does the average entry price drop toward ~0.55 as intended?
- **Win-rate at that cost basis** — is it above the ~56% break-even threshold at 0.55?

Additional caveats:

- **Fill-rate gap**: Large paper P&L does not imply live P&L. The realistic paper model penalizes our queue position and latency, but the actual fill-rate on a Slovakia → us-east-1 path at 110ms RTT with 8th queue position may be lower than modeled.
- **Velocity lag**: `velocity_short` is computed over the last 30 seconds of Binance data. By the time we observe a strong velocity and the Polymarket price is still cheap, the move may already be over.
- **Adverse selection**: If fast participants (Bonereaper, other bots) have already moved the Polymarket price, our "cheap band" entries may be buying into a price that correctly reflects the adverse outcome, not a lagging price with alpha.

A minimum of 2–3 days of paper data with at least 50 fills is needed before drawing conclusions.

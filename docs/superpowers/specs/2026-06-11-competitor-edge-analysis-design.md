# Competitor Edge Analysis — Design

**Date:** 2026-06-11
**Status:** approved (brainstorm), pending implementation plan

## Goal

Measure, from Bonereaper's public on-chain trades, whether his **laddered merge-maker
edge survives his naked-leg losses** — i.e. whether net P&L per window is stably
positive once naked-leg outcomes are subtracted from pair-spread gains. This is the
go/no-go for building a laddered re-quoter for our bot. No trading, no live action —
pure read-only analysis of public data.

## Background

- Our 1-hour live test (2026-06-10) caught hedged pairs at **$0.92–0.99** (edge 1–8¢)
  and lost ~$7.64 — the thin pair edge did not survive 2 naked legs.
- Competitor (Bonereaper, proxy `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30`) caught
  far cheaper pairs, e.g. window 6:30PM ET: **Up 1475@0.598 + Down 1370@0.210 = $0.808**
  (edge +19¢), by laddering resting bids across many price levels and harvesting the
  5-minute volatility (catching the dumping side at extremes like 0.21).
- He also carries large naked/directional positions (e.g. 1321 Up @ 0.888 vs 119 Down).
- **Open question this analysis answers:** is his pair edge genuinely +EV *net of* those
  naked legs, or do the naked legs eat it (the way ours did)?

## Decision

The single go/no-go metric: across a sample of resolved BTC 5m windows,
`Σ pair_pnl` vs `Σ naked_pnl`. Concrete verdict thresholds:

- **BUILD** if `total_net > 0` AND `net_per_window > 0` across the sample.
- **STRONG BUILD** (high confidence) if additionally `total_pair_pnl > 0` on its own
  (the pair edge is positive even before counting naked legs) AND
  `pct_windows_positive > 50%`.
- **DON'T BUILD** if `total_net <= 0`, or if the result hinges entirely on naked-leg
  luck (`total_pair_pnl <= 0` while `total_net > 0` only because naked legs happened
  to win) — that is gambling, not edge.

If BUILD → proceed to design a laddered re-quoter. Otherwise → do not build; reconsider.

## Scope

- **BTC 5m up/down only** (matches our bot). ETH/other deferred unless sample too small.
- **Method B — sampled windows:** ~80–150 recently *resolved* BTC 5m windows. Expand to
  a 24h full pull only if results are borderline.
- Assumption: he holds to resolution (validated ~0% sells), so resolution payout (1/0)
  equals his realized P&L — no need to fetch his redeems.

## Architecture (3 isolated units)

1. **`quoter/analysis/competitor.py`** — PURE logic, no I/O, fully unit-testable:
   - `reconstruct_window(trades, winning_side) -> WindowResult`
   - `aggregate(results) -> Report`
2. **`scripts/analyze_competitor.py`** — I/O glue: paginated pull of his BUY trades for
   BTC 5m markets + per-window resolution; calls the pure logic; prints the report.
3. **`tests/test_competitor_analysis.py`** — unit tests on the pure logic.

## Data model

```
WindowResult:
  window_id: str
  up_shares: float;  up_avg: float        # volume-weighted avg price
  down_shares: float; down_avg: float
  matched: float                          # min(up_shares, down_shares)
  pair_cost: float                        # up_avg + down_avg (only if both sides > 0)
  naked_shares: float                     # |up_shares - down_shares|
  naked_side: "Up" | "Down" | None        # the heavier side (None if perfectly matched)
  naked_avg: float                        # avg price of the naked side
  winning_side: "Up" | "Down"
  pair_pnl: float                         # matched * (1 - pair_cost)
  naked_pnl: float                        # naked_shares * ((1 if naked_side won else 0) - naked_avg)
  net: float                              # pair_pnl + naked_pnl
  spend: float                            # up_shares*up_avg + down_shares*down_avg

Report:
  n_windows, n_hedged (both sides > 0), n_naked (one side or imbalance)
  avg_pair_cost (over hedged windows)
  total_pair_pnl, total_naked_pnl, total_net
  net_per_window, pct_windows_positive
  total_spend, avg_size_per_window
```

## P&L math (per resolved window)

```
up_avg   = Σ(up size·price)   / Σ up size
down_avg = Σ(down size·price) / Σ down size
matched     = min(up_shares, down_shares)
pair_cost   = up_avg + down_avg
pair_pnl    = matched * (1 - pair_cost)
naked_shares = |up_shares - down_shares|
naked_side   = "Up" if up_shares > down_shares else "Down" (None if equal)
naked_pnl    = naked_shares * ((1 if naked_side == winning_side else 0) - naked_avg)
net          = pair_pnl + naked_pnl
```

Aggregate: `Σ pair_pnl`, `Σ naked_pnl`, `Σ net`, `net/window`, `% windows net>0`.

## Data flow (script)

1. Resolve competitor address (constant) and identify BTC 5m up/down markets in a recent
   resolved time range (gamma markets, slug `btc-updown-5m-*`, `closed=true`).
2. For each window market: fetch its winning side (gamma `outcomePrices` / resolved token
   = the outcome whose price is 1).
3. Pull his BUY `TRADE` activity (paginated `limit=500&offset=N`) filtered to those
   markets; group by window; compute volume-weighted Up/Down.
4. Call `reconstruct_window` per window, then `aggregate`.
5. Print the report + verdict.

## Error / edge handling

- **Unresolved windows** → excluded (no payout).
- **Pure one-sided windows** → `pair_pnl = 0`, all P&L in naked.
- **Perfectly matched (naked_shares = 0)** → `naked_pnl = 0`, `naked_side = None`.
- **API truncation / pagination gaps** → the script tracks how many trades/pages it
  pulled and flags in the report if a window looks partial; volume-weighted avg is robust
  to partial fills, but counts may undercount — flagged, and only windows with both a
  resolution and ≥1 trade are scored.
- **Rate limiting** → small sleep between pages; cap total pages.

## Testing

Unit tests on `reconstruct_window` with hand-checked expected values:
- Balanced hedge, hedge side wins (pair_pnl = matched·(1−cost), naked_pnl = 0).
- Balanced hedge, other side wins (same — hedge is direction-independent).
- Imbalanced, naked side WINS (naked_pnl positive).
- Imbalanced, naked side LOSES (naked_pnl = −naked_shares·naked_avg).
- Pure one-sided window (matched = 0).
Plus an `aggregate` test summing a small known set and checking the pair-vs-naked split.

## Deliverable

`scripts/analyze_competitor.py` prints:

```
N windows analyzed (resolved BTC 5m)
Hedged windows: X  | avg pair $0.YY  | Σ pair-PnL +$Z
Naked legs:     M  | Σ naked-PnL +/−$W
NET:  pair +$A  |  naked +/−$B  |  TOTAL $C
Net/window: $D  |  % windows positive: E%
Avg size/window: S shares  |  total spend $T
VERDICT: pair-edge [COVERS / DOES NOT COVER] naked losses  →  [BUILD / DON'T BUILD]
```

A reusable script so we can re-run as we refine (e.g. expand sample, add ETH).

## Out of scope

- Any live trading or order placement.
- Building the laddered re-quoter (separate spec, only if verdict = BUILD).
- Liquidity-rewards estimation (separate question; this analysis isolates pure trade P&L).

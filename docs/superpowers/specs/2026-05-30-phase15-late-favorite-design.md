# Phase-15: Late-Window Favorite-Buying — Design Spec

**Date:** 2026-05-30
**Status:** Approved design (pre-plan)
**Supersedes strategy of:** phase-11/12/13/14 ladder machinery (timing curves, conviction,
directional skew, cheap-tail, symmetric price-cap)

---

## 1. Motivation — what the data proved

We were losing (live paper: **37 resolved markets, 8W/29L, −$282.53**). Analysis of our
own fills + the competitor's real trades overturned the project's prior assumptions.

**Our bot's flaw (confirmed in `state.db`):**
- Buy-only, holds to resolution (0 sells ever) — same as competitor, *not* the problem.
- We quote a **symmetric** ladder on **both** sides and get adversely-selected into the
  **loser**: in **29 of 37** markets we held more of the losing side than the winning side.
- Winning-side book is profitable (+$300); losing-side book (−$582) wipes it out.
- Avg fill prices near fair (NO 0.508 / YES 0.457) — no real edge.

**Competitor (Bonereaper) real trades — 3447 trades, 1.5h, dollar-weighted:**
- **0% sells**, holds to redeem (confirmed buy-and-hold).
- **91%** of money on the **favorite** (price > 0.5); only 8% on a side while < 0.5.
- **81%** of money at prices **> 0.70**.
- **78%** of money in the **second half** of the window; avg price paid rises across
  window quarters: **0.58 → 0.67 → 0.86 → 0.95**.
- **94%** of money is bought **on a rising price** (vs his own prior trade) — he does not
  catch the falling knife.
- Per-market $ is **not** a fixed small cap: median $266, p90 $5542, max $7119 — he sizes
  **up** under certainty.
- In the last quarter, **94%** of money is at price **≥ 0.85** (near-certain outcomes).

**Conclusion:** he does not predict the side — he **follows the price**. Late in the
window the Polymarket price has already converged toward the outcome; he buys the side
that is already winning, scales size as certainty rises, and never adds to a falling side.
His whipsaw protection *is* his entry rule (buy-on-rise + near-certainty), not a separate stop.

**Our phase-14 was exactly backwards on all four axes:** `max_entry_price=0.60` banned his
profit zone (>0.60 = 81% of his money), `entry_cutoff_frac=0.50` stopped us before his edge
window (78% of his money is after 50%), directional filter disabled so we spread across both
sides, and we bought the underdog instead of the favorite.

---

## 2. Goal

Pivot the strategy to **late-window favorite-buying, one side only**, *adapting* the
competitor's insight to our infrastructure (we sit ~110ms from us-east-1, back of queue —
we cannot win the thin 0.95 edge, so we enter earlier/cheaper where the spread and queue
odds are better).

**Decisions locked during brainstorming:**
- **Ambition:** adapt the insight (buy favorite ~0.55–0.85), not literal 0.95 replication.
- **Architecture:** rewrite `compute_ladder` in place (same name/signature); delete
  phase-11/12/13/14 machinery.
- **Whipsaw protection (combination):** buy-on-rise gate + Binance momentum confirmation +
  per-market $ cap scaled by certainty.
- **Placement:** one-sided thin ladder on the favorite (Variant 1) with certainty-scaled
  sizing.
- **No sells** — hold to resolution.

---

## 3. Architecture

`compute_ladder` stays a **pure function**. State needed for the buy-on-rise gate
(previous favorite price) is **passed in as a parameter** — the function stores nothing.
The signature gains one **optional** parameter `prev_mid_yes: float | None = None`; existing
callers keep compiling, but both `quoter_loop` and the backtest engine are updated to pass
it (the engine from the prior series point). All other parameters are unchanged.

```
quoter_loop (per market)
  ├─ tracks prev_mid_yes
  ├─ reads binance velocity_short
  └─ compute_ladder(cfg, mid_yes, time_to_expiry,
                    prev_mid_yes=…, velocity_short=…,
                    inventory_yes_qty=…, inventory_no_qty=…,
                    timeframe=…, asset=…, window_length_sec=…)
        ├─ _pick_favorite_side(mid_yes, velocity_short, cfg)  -> "YES"|"NO"|None
        ├─ buy-on-rise gate (mid_yes vs prev_mid_yes)
        ├─ _certainty_size(price, window_frac, cfg)           -> int
        ├─ _favorite_ladder(side, price, size, cfg)           -> ~3 one-sided bids
        └─ per-market cap filter (inventory cost vs cap)
  → executor posts maker bids → fills accumulate → hold to resolution → redeem
```

Backtest path: replay mid-series → same `compute_ladder` (`prev_mid_yes` from the prior
series point; `velocity_short=None`) → `fillsim` → resolution PnL.

---

## 4. Components

### 4.1 `quoter/config.py`

**Add (phase-15):**
| Field | Default | Meaning |
|---|---|---|
| `favorite_min_price` | `0.55` | below this there is no clear favorite → no quotes |
| `max_entry_price` | `0.85` | hard ceiling on any bid (raised from 0.60) |
| `entry_start_frac` | `0.30` | no entries before this fraction of the window |
| `dead_zone_half_width` | `0.05` | no quotes when `|mid_yes − 0.5| < this` |
| `certainty_size_base` | `5` | base shares per tick (Polymarket min) |
| `certainty_size_max` | `40` | cap on per-tick shares at max certainty |
| `per_market_cap_usd` | `50.0` | base $ ceiling per market |
| `certainty_cap_multiplier` | `2.0` | cap may scale up to ×this under high certainty |
| `velocity_confirm_threshold` | `0.0005` | min Binance velocity (abs) to confirm direction |
| `rise_tolerance_cents` | `0.01` | favorite price may dip up to this vs prev and still quote |
| `favorite_ladder_levels` | `3` | one-sided bids per tick |
| `min_time_to_expiry_sec` | `5.0` | below this → no quotes |

**Remove (phase-11/12/13/14, no longer used):** `timing_curve_5m`, `timing_curve_15m`,
`late_window_*`, `conviction_*`, `directional_filter_enabled`,
`directional_high_threshold`, `directional_low_threshold`,
`directional_size_skew_enabled`, `directional_skew_coef`, `polarized_threshold`,
`polarized_cheap_side_pct`, `cheap_tail_levels`, `tight_cluster_*`, `entry_cutoff_frac`.
Keep generic infra knobs (`bankroll`, paper-fill realism, risk caps, paths, endpoints,
`requote_*`, velocity lookbacks).

`from_env()` still overrides only `mode` / `bankroll_usd` / `log_level`; all strategy knobs
use dataclass defaults (so phase-15 defaults are active in shadow/paper/live).

### 4.2 `quoter/strategy/ladder.py` — rewritten `compute_ladder`

Helpers:
- `_pick_favorite_side(mid_yes, velocity_short, cfg) -> Side | None`
  - if `|mid_yes − 0.5| < dead_zone_half_width` → `None`
  - favorite = `"YES"` if `mid_yes > 0.5` else `"NO"`; its price = `max(mid_yes, 1−mid_yes)`
  - if `velocity_short is not None`: require it agrees with favorite direction
    (`YES` favorite needs `velocity_short ≥ +threshold`; `NO` needs `≤ −threshold`);
    otherwise `None`
  - if `velocity_short is None` (backtest): skip the velocity check (mid-only)
- `_certainty_size(price, window_frac, cfg) -> int`
  - certainty score in `[0,1]` rising with both `price` (over `favorite_min_price..max_entry_price`)
    and `window_frac` (over `entry_start_frac..1.0`); size interpolates
    `certainty_size_base..certainty_size_max`, rounded to a whole number ≥ base
- `_favorite_ladder(side, fav_price, size, cfg) -> list[Quote]`
  - `favorite_ladder_levels` bids from a floor up to `min(fav_price − 0.01, max_entry_price)`,
    each priced ≤ `max_entry_price`, all on `side`, each `size` shares

`compute_ladder` orchestration (early-returns make every edge explicit):
1. guards: `min(mid)` valid, `time_to_expiry ≥ min_time_to_expiry_sec`
2. `window_frac` from `time_to_expiry` / window length; if `< entry_start_frac` → `[]`
3. `side = _pick_favorite_side(...)`; if `None` → `[]`
4. `fav_price = max(mid_yes, 1−mid_yes)`; if `< favorite_min_price` → `[]`
5. buy-on-rise: if `prev_mid_yes` given and the favorite's price fell by more than
   `rise_tolerance_cents` vs its previous value → `[]`
6. per-market cap: current spent on this market (from inventory cost) ≥
   `per_market_cap_usd × certainty_cap_multiplier`-scaled ceiling → `[]`
7. `size = _certainty_size(fav_price, window_frac, cfg)`
8. `return _favorite_ladder(side, fav_price, size, cfg)` (already ≤ max_entry_price,
   one-sided so no self-cross possible)

### 4.3 `quoter/quoter_loop.py`

Track `prev_mid_yes` per market (the loop already watches mid moves for requote). Pass it
and `velocity_short` into `compute_ladder`. No sell logic added.

### 4.4 `quoter/backtest/`

The config-toggle A/B (baseline phase-13 vs new) no longer applies — we rewrote the
function. New comparison:
- Run the new `compute_ladder` over cached resolved markets (and an expanded sample if
  available); `prev_mid_yes` comes from the prior series point; `velocity_short=None`.
- Compare against the **recorded live baseline −$282.53** from `state.db`.
- Emit a report in `reports/` with a per-market table and honest caveats.
- **Invariant preserved:** the engine calls the real `compute_ladder`, not a copy.

---

## 5. Edge handling (explicit rules)

| Condition | Behavior |
|---|---|
| `mid` in dead zone around 0.5 | `[]` (no clear favorite) |
| `window_frac < entry_start_frac` | `[]` (let price settle) |
| `fav_price < favorite_min_price` | `[]` (favorite not certain enough) |
| bid price > `max_entry_price` | dropped (thin edge) |
| favorite price fell vs `prev_mid_yes` beyond tolerance | `[]` (anti-knife) |
| velocity disagrees with direction (live) | `[]` (momentum not confirming) |
| `velocity_short is None` (backtest/missing) | skip velocity gate (mid-only) |
| per-market cap reached | `[]` (stop adding) |
| `time_to_expiry < min_time_to_expiry_sec` | `[]` (too late / won't fill) |

No sells under any condition — hold to resolution.

---

## 6. Testing

### Unit (`tests/test_ladder.py` — rewritten)
| Test | Asserts |
|---|---|
| `test_picks_higher_side_as_favorite` | mid>0.5 → YES bids; mid<0.5 → NO bids |
| `test_dead_zone_no_quotes` | mid≈0.5 → `[]` |
| `test_too_early_no_quotes` | `window_frac < entry_start_frac` → `[]` |
| `test_below_min_price_no_quotes` | favorite < 0.55 → `[]` |
| `test_caps_at_max_entry_price` | no bid price > 0.85 |
| `test_falling_favorite_suppressed` | mid fell vs prev → `[]` |
| `test_velocity_disagree_blocks` | velocity against direction → `[]` |
| `test_velocity_none_falls_back` | velocity=None → not blocked |
| `test_certainty_size_monotonic` | higher price/later window → size non-decreasing |
| `test_per_market_cap_stops_adds` | at cap → `[]` |
| `test_only_favorite_side` | every bid on one side |

Delete phase-11/12/13/14 tests whose logic no longer exists.

### Backtest
- New `compute_ladder` over cached markets (+ expanded sample if available).
- Compare to recorded live baseline (−$282.53); per-market table + caveats in `reports/`.
- Invariant: engine calls the real `compute_ladder`.

---

## 7. Success criteria

1. **Backtest:** new `compute_ladder` beats the recorded live baseline (−$282.53) on both
   total PnL **and** worst-case market, on the cached sample.
2. **Anti-overfit:** the edge holds on an expanded sample (target ≥50 markets), not
   concentrated in 1–2 markets.
3. **Final confirmation is paper only:** the backtest does **not** measure fill-rate; the
   real proof is a positive/break-even paper run, because a thin-edge favorite-buying
   tactic lives or dies on whether our bids actually fill at these prices given our
   latency and queue position.

**Honest framing:** the backtest is a *filter against the obviously-bad*, not authorization
for live capital. Paper is the real test.

---

## 8. Out of scope

- Selling / spread capture / inventory flattening (competitor doesn't do it either; our
  infra can't either).
- Literal 0.95 last-second replication (fill-rate infeasible for us).
- Live trading with real capital (separate decision after paper validation).

## 9. Sources

- Our live data: `state.db` (37 resolved markets, −$282.53).
- Competitor real trades: `data-api.polymarket.com/activity` for
  `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30` (3447 trades, dollar-weighted analysis).
- Prior reports: `reports/2026-05-28_why-minus-vs-bonereaper.md`,
  `reports/2026-05-28_backtest-baseline-vs-new.md`.

# Phase-18: Early Momentum-Entry (cost-basis fix) — Design Spec

**Date:** 2026-06-08
**Status:** Approved (design approved by user)
**Goal:** Replace the favorite leg with a velocity-driven early-entry leg that buys the side
Binance momentum favors WHILE IT IS STILL CHEAP (~0.40–0.65), to fix the proven cost-basis
problem. Paper only.

## Why (quantified)
Head-to-head over 16 windows shared with competitor Bonereaper: our side-selection hit-rate
(~63–69%) MATCHES his, but our average entry price is **0.81** vs his **0.54**. At a ~69%
hit-rate the EV is decisive: buying at 0.54 = **+$0.15/share (profitable)**, buying at 0.81 =
**−$0.18/share (losing)**. Same windows, same sides, opposite EV — entirely cost basis. Our
phase-16/17 favorite-buying structurally fills at 0.81 (it buys the CURRENT favorite and fills
as the price rises). Config tweaks did not lower it. The only fix is to enter the eventual
winner EARLY/CHEAP — which is what momentum-entry does.

---

## 1. The change (replace `_favorite_leg` with `_momentum_leg`)

`compute_ladder` becomes `_momentum_leg(...) + _lottery_leg(...)`. The favorite leg and its
helpers (`_favorite_leg`, `_certainty`, `_pick_favorite_side`) are removed. `_favorite_ladder`,
`_lottery_leg`, `Quote`, `Side` stay.

### `_momentum_leg(cfg, mid_yes, velocity_short, inventory_yes_qty, inventory_no_qty)`
1. **Signal gate:** if `velocity_short is None` or `abs(velocity_short) < momentum_velocity_threshold`
   → `[]`. (No signal; also disables the leg in the backtest, where velocity is None.)
2. **Side from momentum:** `velocity_short > 0` → buy `YES`; `< 0` → buy `NO`. (Direction of the
   Binance move, NOT the current favorite.)
3. **Cheap band only:** `price = mid_yes` (YES) or `1−mid_yes` (NO). Buy only if
   `momentum_min_price ≤ price ≤ momentum_max_price` (default 0.40–0.65). Below the floor →
   too uncertain; above the ceiling → the cheap entry was missed, buying expensive is −EV → `[]`.
4. **Commit-to-one-side** (reuse phase-17 $-value threshold, robust to lottery deadlock):
   `max_lottery_usd = lottery_cap_usd + lottery_size*lottery_levels*lottery_max_price`;
   block the opposite side if the held side's $-value exceeds it.
5. **Per-market cap** (flat, no certainty scaling): if `side_qty * price ≥ per_market_cap_usd` → `[]`.
6. **Bids:** `_favorite_ladder(side, price, flat_size, cfg)` (reused; price is in the cheap band,
   well below `max_entry_price`, so its ceiling has no effect).
7. Buy-only, held to resolution (no sells).

### Side note: the velocity sign convention
`get_binance_velocity` returns a fraction (e.g. +0.001 = +0.1% over the lookback). Positive =
asset moving up = "Up"/YES more likely. The existing `velocity_short` plumbing
(quoter_loop → compute_ladder) is unchanged.

---

## 2. Config knobs (new) + phase-A whitelist

| Knob | Default | Range (phase-A) |
|---|---|---|
| `momentum_velocity_threshold` | 0.001 | 0.0 – 0.02 |
| `momentum_min_price` | 0.40 | 0.20 – 0.60 |
| `momentum_max_price` | 0.65 | 0.50 – 0.90 |

Added to `LiveSettings._SPEC` and the dashboard `SETTING_KEYS`. The old favorite knobs
(`favorite_min_price`, `entry_start_frac`, `max_entry_price`, `rise_tolerance_cents`,
`certainty_cap_multiplier`, `velocity_confirm_threshold`) remain in `Config` but are now
**unused/deprecated** (kept to avoid whitelist/settings churn; may be removed in a later
cleanup). `flat_size`, `per_market_cap_usd`, `favorite_ladder_levels`, `min_time_to_expiry_sec`
and all lottery knobs stay in use.

---

## 3. Backtest — KNOWN LIMITATION

The offline backtest passes `velocity_short=None` (no Binance history), so `_momentum_leg`
returns `[]` for every interval — **the momentum strategy cannot be evaluated offline.** The
backtest will show ~zero momentum activity. This is a hard limitation: **phase-18 can only be
validated in paper** (and ultimately co-located live). The backtest runner is left runnable
(it will report near-zero PnL for the momentum config) with this caveat documented in the
report.

---

## 4. Testing

Unit (`tests/test_ladder.py`, rewritten for the momentum leg; velocity_short is now REQUIRED
for any momentum bid):
| Test | Asserts |
|---|---|
| `test_no_velocity_no_quotes` | velocity_short=None → no momentum bids (only lottery if cheap) |
| `test_weak_velocity_no_quotes` | abs(velocity) < threshold → no momentum bids |
| `test_momentum_buys_up_side` | velocity +0.01, mid 0.55 → YES bids in band |
| `test_momentum_buys_down_side` | velocity −0.01, mid 0.45 → NO bids (underdog price 0.55 in band) |
| `test_momentum_skips_too_expensive` | velocity +0.01, mid 0.80 → price 0.80 > max → no momentum bids |
| `test_momentum_skips_too_cheap` | velocity +0.01, mid 0.30 → price 0.30 < min → no momentum bids |
| `test_momentum_commit_one_side` | hold YES > threshold → down-velocity NO momentum blocked |
| `test_momentum_cap_stops` | large side inventory → cap fires → no momentum bids |
| `test_momentum_flat_size` | every momentum bid size == flat_size |
| (lottery tests retained) | lottery leg behavior unchanged |

Backtest test (`tests/test_backtest_engine.py`): update the favorite-profit scenario — with
velocity None the momentum leg never fires, so a pure-momentum backtest market has no fills;
assert that (document the limitation in the test). Keep the no-fill test.

Full suite green.

---

## 5. Success criteria
1. The bot buys the velocity-favored side in the cheap band (0.40–0.65) → realized cost basis
   drops from ~0.81 toward ~0.55 (measured live from paper fills).
2. Side hit-rate stays comparable (~60%+); with the lower cost basis the per-share EV turns
   positive.
3. All knobs live-editable; full suite green; paper only.
4. **Honest gate:** this is an UNPROVEN hypothesis (that Binance velocity predicts the winner
   well enough at 0.45–0.60 to be +EV). The backtest cannot test it. Only a paper run with
   measured cost basis AND win-rate can validate it. Big paper P&L ≠ live (fill-rate).

## 6. Out of scope
- Live trading (paper only).
- Removing the deprecated favorite knobs (later cleanup).
- The lottery leg (unchanged, separately toggleable).

## 7. Sources
- Cost-basis finding: parallel analysis of `state.db` vs competitor trades
  (`data-api.polymarket.com/activity` for `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30`):
  our avg entry 0.81 vs his 0.54, same ~69% hit-rate.
- Velocity plumbing: `quoter_loop.py` `get_binance_velocity` → `velocity_short`.

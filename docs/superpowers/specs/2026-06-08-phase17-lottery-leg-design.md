# Phase-17: Cheap-Tail Lottery Leg (full competitor identity) — Design Spec

**Date:** 2026-06-08
**Status:** Approved (design approved by user)
**Goal:** Make the bot's tactic match the competitor's BIMODAL profile by adding a small
cheap-tail lottery leg on the underdog, in parallel with the existing favorite "dollar
engine". The favorite leg keeps commit-one-side (the competitor does NOT flip his main bet —
his two-sidedness IS the lottery).

**Scope constraint:** Paper only. No live-trading code.

**Context:** Competitor profile (fresh): 82% of money on the favorite, but only ~46% of
TRADES by count are favorite (avg trade price 0.47) — he sprinkles many small cheap underdog
lottery tickets. 84% one-side concentration by dollars. Our bot is currently strictly
one-sided favorite (100%), avg price 0.74 — missing the cheap lottery. The lottery is ~3-4%
of his money (cosmetic, not his profit driver); this phase closes the identity gap without
changing the profitable favorite engine.

---

## 1. The change (all inside `compute_ladder`)

Add a SECOND leg alongside the existing favorite leg. `compute_ladder` returns
`favorite_bids + lottery_bids`.

### Favorite leg — extracted, one adjustment
The existing phase-16 logic (entry_start_frac gate, favorite-side pick, commit-one-side,
favorite_min_price, buy-on-rise, per-market cap, flat size) is extracted into a helper
`_favorite_leg(...)`. It still commits to one side and never flips.

**One required adjustment to commit-one-side:** the lottery fills the OPPOSITE (underdog)
side, so a small lottery holding must NOT trip the favorite leg's commit gate. The check
changes from "hold the opposite side at all" to "hold the opposite side *more*":
```python
if inventory_yes_qty > inventory_no_qty and side == "NO":
    return []
if inventory_no_qty > inventory_yes_qty and side == "YES":
    return []
```
This is **identical to phase-16 when the lottery is off** (only one side is ever held, so
`held_side_qty > 0 = other_side_qty`), and correctly keeps the favorite committed to its
majority side when small lottery inventory exists on the other side (favorite shares always
exceed lottery shares: favorite cap $50 ≫ lottery cap $3).

### Lottery leg — NEW (`_lottery_leg`)
Small buys on the UNDERDOG (cheap) side, matching the competitor's lottery sprinkle.
- Underdog side/price: `side = "NO", price = 1−mid_yes` if `mid_yes ≥ 0.5`, else
  `side = "YES", price = mid_yes`. (The side priced below 0.5.)
- Gate: only if `underdog_price ≤ lottery_max_price` (new knob, default **0.40**).
- Own small budget: `lottery_cap_usd` (new, default **3.0**) — SEPARATE from
  `per_market_cap_usd`. Spent proxy = `underdog_qty × underdog_price`; stop when ≥ cap.
- Size: `lottery_size` shares per bid (new, default **5**). Levels: `lottery_levels` cheap
  bids descending 1c from `underdog_price` (new, default **2**).
- **Exempt from** commit-one-side, `entry_start_frac`, velocity confirmation, and
  `favorite_min_price` — the competitor sprinkles lottery throughout the window on the cheap
  side regardless of those favorite-leg gates.
- Buy-only, held to resolution (like everything).
- Disable switch: `lottery_size = 0` or `lottery_levels = 0` ⇒ no lottery bids.

No self-cross possible: favorite bid (high) + underdog lottery bid (low) sum < 1.0 (they are
complementary prices, each bid is below its side's price).

---

## 2. Config knobs (new) + phase-A whitelist

| Knob | Default | Range (phase-A) |
|---|---|---|
| `lottery_max_price` | 0.40 | 0.10 – 0.49 |
| `lottery_cap_usd` | 3.0 | 0 – 50 |
| `lottery_size` | 5 | 0 – 50 (0 disables) |
| `lottery_levels` | 2 | 0 – 5 (0 disables) |

All four added to `LiveSettings._SPEC` (the phase-A whitelist) so they are live-editable on the
dashboard. The dashboard form's `SETTING_KEYS` list gains the four keys.

---

## 3. Backtest

- Run the real `compute_ladder` (invariant) over cached markets with phase-17 defaults;
  compare to phase-16 (lottery off, i.e. `lottery_size=0`) to measure what the lottery adds or
  subtracts. Report in `reports/`.
- The engine already passes inventory, so the lottery cap and favorite commit-one-side both
  work in backtest.

---

## 4. Testing

Unit (`tests/test_ladder.py`, extend — note the favorite-leg tests still hold; the
`test_only_favorite_side` premise changes because the lottery now adds underdog bids):
| Test | Asserts |
|---|---|
| `test_favorite_leg_unchanged` | favorite bids identical to phase-16 for a clear-favorite mid (e.g. 0.90 → YES bids present) |
| `test_lottery_adds_underdog_bids` | mid 0.90 → output contains NO bids at ≤ lottery_max_price |
| `test_lottery_price_band` | underdog price > lottery_max_price (e.g. mid 0.55 → underdog 0.45 > 0.40) → no lottery bids |
| `test_lottery_size_zero_disables` | `lottery_size=0` → no lottery bids (favorite only) |
| `test_lottery_cap_stops` | large underdog inventory → lottery cap fires → no lottery bids |
| `test_lottery_exempt_from_commit` | holding favorite YES still allows NO lottery bids (commit-one-side does not block lottery) |
| `test_lottery_exempt_from_entry_start` | early window (window_frac < entry_start_frac) → favorite leg empty BUT lottery still bids if underdog cheap |
| `test_both_sides_present` | a clear favorite with cheap underdog → output has both YES and NO bids (bimodal) |

Replace the old `test_only_favorite_side` (no longer true by design).

Backtest test (`tests/test_backtest_engine.py`): keep the favorite-profit scenario; it stays
green (lottery adds at most tiny underdog bids that don't break it).

Full suite green.

---

## 5. Success criteria

1. Bot now quotes BOTH a favorite leg and a cheap-underdog lottery leg → profile becomes
   bimodal like the competitor (favorite dollars + small cheap-tail).
2. Favorite engine behavior is byte-for-byte unchanged (commit-one-side intact, no flip).
3. Lottery is bounded by its own small cap; disabling via `lottery_size=0` reverts to phase-16.
4. All four lottery knobs are live-editable on the dashboard.
5. Backtest shows the lottery's marginal effect (expected: small, near-neutral — it is ~3-4%
   of money, not the profit driver).
6. Full suite green; paper only.

**Honest framing:** this achieves *profile identity* with the competitor. It is NOT expected to
materially change PnL — the lottery is cosmetic coverage, not his edge. The profitable part
(favorite engine) is already replicated and unchanged.

## 6. Out of scope
- Live trading (paper only).
- Changing the favorite engine.
- Selling / spread capture.

## 7. Sources
- Competitor fresh analysis: `data-api.polymarket.com/activity` for
  `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30` (82% money favorite / 46% count, avg 0.47).
- phase-16 favorite engine: `quoter/strategy/ladder.py`.
- phase-A live settings: `quoter/ops/live_settings.py`.

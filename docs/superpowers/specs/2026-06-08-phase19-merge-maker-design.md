# Phase-19: Two-Sided Merge-Maker (the competitor's REAL edge) — Design Spec

**Date:** 2026-06-08
**Status:** Approved (design approved by user; sections 1–3 confirmed)
**Goal:** Replace the directional favorite/momentum strategy with a two-sided
market-maker that posts maker bids on BOTH Up and Down at a target pair cost
below $1.00, then merges the matched complementary pairs back to $1.00 — locking
the maker spread regardless of direction. Paper only.

## Why (proven, not hypothesized)

We analysed 3 386 of competitor Bonereaper's real trades and his own fills prove
his edge is NOT directional prediction:

- **His per-entry win-rate is ~47% — a coin flip.** He does not pick winners.
- **91% of his windows are two-sided** (he buys both Up and Down).
- **Median pair cost (avg_Up + avg_Down) = $0.993** — he buys complementary
  pairs for less than $1.00 and MERGEs them to $1.00, locking ~$0.01–0.02/share.
- Estimated locked merge profit over a 51-min sample: **$655 on 31 806 merged
  shares ≈ $0.021/share**, which extrapolates to ~$18k/day — matching his known
  ~$14k/day. The bimodality (cheap leg ~0.23 + expensive leg ~0.76) is simply
  the two legs of his merge pairs.

Live-book measurement confirms the edge is available to us: across 111 samples of
live BTC/ETH up-down books, **best_bid_Up + best_bid_Down < $1.00 in 100% of
samples** (median $0.990, capturable edge 1–2¢/pair, depth ~89 shares). Taker
pair (ask+ask) was $1.010 — no free arb; the edge lives purely in the maker
spread, so we must POST, not take.

Our own paper fills prove the OTHER half: our cheap fills win only **16.7%**
(adverse selection from 110 ms / back-of-queue). Our directional phases
(15–18) are therefore structurally −EV and are being **fully replaced**.

**The competitor is not a better forecaster — he runs a different game.** This
phase moves us to that game.

---

## 1. Architecture & data flow

`compute_ladder` is rewritten from a directional entry into a two-sided
merge-maker. It stays a **pure, stateless function** (inputs: cfg, mid, current
inventory; output: list[Quote]) — matching the existing project pattern.

```
quoter_loop (requote)
  └─ compute_ladder(cfg, mid_yes, inventory_yes_qty, inventory_no_qty)
       └─ bids on BOTH sides: Up @ mid−δ, Down @ (1−mid)−δ   (pair = 1 − 2δ)
  └─ paper_executor fills (unchanged)
  └─ _apply_paper_fills (drain):
       ├─ inv.on_fill(...)            (unchanged)
       └─ inv.on_merge(matched)      ← NEW: merge matched pairs immediately
```

**Files:**
- `quoter/strategy/ladder.py` — rewrite `compute_ladder`; add `_merge_ladder`;
  remove `_momentum_leg`, `_lottery_leg`, `_favorite_ladder` and the legacy
  kwargs. Keep `Quote`, `Side`.
- `quoter/quoter_loop.py` — add the merge step to `_apply_paper_fills`; simplify
  the `compute_ladder` call (drop `velocity_short`, `velocity_long`,
  `prev_mid_yes`, `committed_side`, `time_to_expiry` extras no longer needed —
  keep the time-to-expiry gate in the loop, see §4). Remove `_prev_mid_yes`
  bookkeeping if unused elsewhere.
- `quoter/config.py` — add merge knobs; remove directional + lottery knobs.
- `quoter/ops/live_settings.py` + `quoter/ops/dashboard.py` — update the
  whitelist `_SPEC` and the dashboard `SETTING_KEYS` to the new knob set.
- `quoter/strategy/inventory.py` — **unchanged** (`on_merge`/`matched` already
  exist and are correct).

**Persistence:** merge mutates `inv.positions` (reduces qty + cost at the matched
average) and adds the spread to `inv.realized_pnl`. The existing 1 Hz snapshot
loop (`state.upsert_position`) persists the reduced positions automatically; the
dashboard headline `realized_pnl` already reflects merge profit. No new
persistence wiring. (Caveat: per-market `markets.resolved_pnl` in SQLite records
only the resolution leg, not the mid-window merge profit; the authoritative
total is `inv.realized_pnl`. Acceptable for paper.)

---

## 2. Pricing + balance algorithm (the heart)

`_merge_ladder(cfg, mid_yes, inventory_yes_qty, inventory_no_qty) -> list[Quote]`:

```python
delta = cfg.merge_edge / 2.0                 # per-leg discount below mid
up_price = round(mid_yes - delta, 2)
dn_price = round((1.0 - mid_yes) - delta, 2)

# Gate 1 — EDGE: after rounding, the pair must still cost < $1.00
if up_price + dn_price >= 1.0:
    return []

# Gate 2 — CAPITAL: stop once this market's spend hits its cap
if (inventory_yes_cost + inventory_no_cost) >= cfg.per_market_cap_usd:
    return []
#   (cost totals read from the Position; see note below)

# Gate 3 — BALANCE: add a leg only if it does NOT push us further past the
# naked cap on that side. Suppress the side we are already long of.
cap = cfg.max_naked_shares
post_up   = (inventory_yes_qty - inventory_no_qty) < cap
post_down = (inventory_no_qty - inventory_yes_qty) < cap

bids = []
if post_up and up_price > 0.0:
    bids += _ladder("YES", up_price, cfg.flat_size, cfg.merge_levels)
if post_down and dn_price > 0.0:
    bids += _ladder("NO",  dn_price, cfg.flat_size, cfg.merge_levels)
return bids
```

`_ladder(side, top_price, size, levels)` — `levels` bids descending 1¢ from
`top_price`, skipping any price ≤ 0 (a small generalization of the old
`_favorite_ladder`, with no `max_entry_price` ceiling — merge prices are mid-band
and never hit it).

**Note on the capital gate inputs:** `_merge_ladder` needs the cost totals, not
just quantities, to enforce `per_market_cap_usd`. The caller passes
`inventory_yes_cost` / `inventory_no_cost` (from the `Position`) alongside the
quantities. Signature: `_merge_ladder(cfg, mid_yes, yes_qty, no_qty, yes_cost,
no_cost)`.

**Why these gates are the loss-minimizer (derived from the data):** the
competitor tolerates ~42% naked residual because at his latency the naked leg is
bought cheap and is ~coin-flip (EV-neutral). OUR naked leg is toxic (16.7%,
adverse-selected), so we balance MORE aggressively than he does:
1. Pair edge is baked into price — we never buy a pair ≥ $1.
2. The balance gate hard-caps naked exposure at `max_naked_shares`: once long one
   side by the cap, we stop adding to it and post only the short side.
3. We never "chase" a leg that ran away (no bidding above `mid−δ`); if it doesn't
   fill, naked stays within cap and the matched portion merges.

---

## 3. Merge trigger

In `_apply_paper_fills`, after draining fills into inventory, merge all currently
matched pairs for each affected market:

```python
for f in fills:
    self.inv.on_fill(...)
    ...
# after the fill loop, for the market just drained:
pos = self.inv.positions.get(market_id)
if pos is not None and pos.matched > 0:
    self.inv.on_merge(market_id, pos.matched)
    log.info("paper_merge", market=market_id[:12], pairs=...)
```

Continuous merging locks the spread immediately, recycles capital, and makes it
physically impossible for an already-matched pair to later become an unbalanced
(naked) loss. `inventory.on_merge` is already capped at `matched` and safe.

Merge is PnL-equivalent to holding the balanced pair to resolution (one leg pays
$1, the other $0 = exactly the merge's $1), so it adds no fake profit — its value
is risk-capping and capital recycling. In LIVE this would be an on-chain CTF
`mergePositions` tx; **that on-chain path is out of scope (paper only).**

---

## 4. Config knobs

### New (live-editable via phase-A)
| Knob | Default | Range | Purpose |
|---|---|---|---|
| `merge_edge` | 0.01 | 0.002–0.04 | target total edge per pair (per-leg δ = /2) |
| `max_naked_shares` | 20 | 0–200 | hard cap on \|yes_qty − no_qty\| |
| `merge_levels` | 2 | 1–5 | bids per side per tick |

### Kept (in use)
`flat_size`, `per_market_cap_usd`, `min_time_to_expiry_sec`, all paper-fill /
risk / WS / Binance-buffer knobs.

### Removed (full replace — delete from `config.py`, `live_settings._SPEC`,
### dashboard `SETTING_KEYS`, and all tests)
`favorite_min_price`, `max_entry_price`, `entry_start_frac`,
`certainty_cap_multiplier`, `velocity_confirm_threshold`, `rise_tolerance_cents`,
`favorite_ladder_levels`, `momentum_velocity_threshold`, `momentum_min_price`,
`momentum_max_price`, `lottery_max_price`, `lottery_cap_usd`, `lottery_size`,
`lottery_levels`. The Binance velocity buffer plumbing (`velocity_*_lookback`,
`get_binance_velocity`) may remain wired but is no longer consumed by the
strategy; leave it to avoid churn (it is cheap and harmless), or remove if
trivial — implementer's discretion, but do not break startup.

### Time-to-expiry gate
Keep the existing `min_time_to_expiry_sec` guard. It stays in `compute_ladder`
(or the loop) as a pre-check: `if time_to_expiry < cfg.min_time_to_expiry_sec:
return []`. The mid sanity bound `0.02 ≤ mid_yes ≤ 0.99` also stays.

---

## 5. Risk limits
- **`max_naked_shares`** — the key new guard: realized naked exposure ≤ cap × $1.
- **`per_market_cap_usd`** — ceiling on accumulated spend per market (unchanged).
- **`RiskGuard.max_daily_loss_usd`** — global halt (unchanged).

---

## 6. What paper shows (honest) + success criteria

Our paper fill model fills a bid only when the ask DROPS below it (catching the
falling leg) — it simulates 110 ms / back-of-queue. So in paper the bot will fill
mostly the falling leg, accumulate naked exposure up to `max_naked_shares` (then
the balance gate stops it), and merge whatever matched it manages to collect.
**Expected paper P&L is ~zero-to-slightly-negative, bounded by
`max_naked_shares` — NOT positive.**

This is the same honest limitation as phase-18, with one decisive difference: the
edge here is **measured to exist in the live book (100%)**, whereas the phase-18
predictive signal did not exist. Real profitability is realizable only **live in
us-east-1**, where we sit at the front of the queue and fill BOTH legs.

**Paper success criteria (NOT profit):**
1. The bot posts two-sided bids with pair cost < $1.00.
2. Naked exposure never exceeds `max_naked_shares`.
3. Merges fire on matched pairs and `inv.realized_pnl` reflects the locked spread.
4. All knobs live-editable on the dashboard; full test suite green; paper only.

---

## 7. Testing (`tests/test_ladder.py` rewritten)

| Test | Asserts |
|---|---|
| `test_posts_both_legs` | mid 0.55 → YES bids at 0.545→ and NO bids present |
| `test_pair_below_one` | best YES bid + best NO bid < $1.00 |
| `test_no_edge_skips` | rounding makes pair ≥ $1 → empty |
| `test_balance_gate_suppresses_long_side` | yes−no ≥ cap → only NO bids |
| `test_naked_capped` | simulated repeated one-leg fills never exceed cap |
| `test_per_market_cap_stops` | cost ≥ cap → empty |
| `test_extreme_mid_skips_leg` | mid 0.99 → the ≤0-priced leg is skipped |
| `test_merge_edge_widens_spread` | larger `merge_edge` → deeper (lower) bid prices |
| `test_time_to_expiry_gate` | tte < min → empty |
| merge-step test | after balanced fills, `on_merge` runs and realized_pnl = spread |

Update `tests/test_backtest_engine.py` and any test referencing removed knobs.
Backtest still runs the real `compute_ladder` (invariant); with mid available it
will post two-sided bids, but the offline fill model is unchanged — report is
descriptive, not a profit claim. Full suite green.

---

## 8. Out of scope
- Live trading and the on-chain CTF `mergePositions` tx (paper only).
- us-east-1 deployment (separate effort; this builds the strategy that needs it).
- Selling / exiting a naked leg (we cap it, we do not sell — buy-only maker).
- Any directional signal (fully removed).

## 9. Sources
- Competitor analysis scripts: `scripts/analyze_competitor_edge.py`,
  `scripts/_two_sided.py`, `scripts/_merge_edge_large.py`,
  `scripts/_real_cases.py`, `scripts/_balance.py` (data-api activity for
  `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30`).
- Live-book feasibility: `scripts/_live_merge_feasibility.py` (CLOB `/book`).
- Our adverse-selection measurement: `state.db` fills × resolutions by price
  bucket (cheap fills win 16.7%).
- Merge accounting: `quoter/strategy/inventory.py` `on_merge` / `matched`.

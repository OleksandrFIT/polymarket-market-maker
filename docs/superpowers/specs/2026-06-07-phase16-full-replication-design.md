# Phase-16: Full-Replication Tactic — Design Spec

**Date:** 2026-06-07
**Status:** Approved (Section 1 approved by user; user delegated remainder and stepped away)
**Goal:** Make the bot's tactic match the competitor Bonereaper's measured profitable
profile — late-window, high-price (near-certain) favorite-buying, one side only.

**Scope constraint (user):** Paper mode only. Do NOT implement or enable live trading.

**Context:** Live paper analysis of phase-15 (50 resolved 5m windows): 70% win-rate,
+$17 net, dragged down by a few oversized one-sided favorites that flipped
(−$47 / −$35 / −$31). Diagnosis: position SIZE and two-sided accumulation on flips are
the loss drivers; entry profile is too early/cheap vs the competitor.

Competitor measured profile (rolling 5m/15m, dollar-weighted): 92% favorite, **70% of money
at price ≥0.95**, **64% in the last 40% of the window**, **94% buy-on-rise**, 0% sells,
~91% one-side concentration. We now target this directly (latency to be solved later by AWS
us-east-1 deployment, so fill-rate is out of scope for this phase).

---

## 1. Changes (all inside `compute_ladder` + config values)

Four changes, no new modules.

### 1a. Late + high entry (match competitor price/timing profile)
Config value changes only (the gates already exist in `compute_ladder`):
| Knob | phase-15 | phase-16 |
|---|---|---|
| `favorite_min_price` | 0.55 | **0.85** (only near-certain favorites) |
| `entry_start_frac` | 0.30 | **0.60** (only the last 40% of the window) |
| `max_entry_price` | 0.95 | **0.97** (allow ≥0.95, like him) |

### 1b. Commit-to-one-side (eliminate two-sided flip accumulation)
`compute_ladder` already receives `inventory_yes_qty` / `inventory_no_qty`. Add: once we
hold a side this window, quote ONLY that side. The first FILL locks the side for the rest of
the window; a favorite flip can no longer make us accumulate the opposite side.
```python
# After the favorite side is chosen:
if inventory_yes_qty > 0 and side == "NO":
    return []
if inventory_no_qty > 0 and side == "YES":
    return []
```

### 1c. Flat per-tick size (stop ramping size up into the price)
phase-15's `_certainty_size` grows with price → biggest size at the highest price → maximum
loss when a high favorite flips (the −$60 mechanism). Replace with a flat fixed size. Because
phase-16 only ever enters in the narrow ≥0.85 / last-40% band, certainty is near-max anyway,
so flat sizing loses nothing and removes the peak-loading risk.
- New knob `flat_size: int = 10`.
- `compute_ladder` uses `size = cfg.flat_size` instead of `_certainty_size(...)`.

### 1d. Per-market cap — UNCHANGED (user decision)
Keep `per_market_cap_usd` (50) and the existing certainty-scaled cap formula as-is; the user
will tune it live in a later phase (dashboard live-config). NOTE: with commit-to-one-side the
per-side cap is now effectively per-window (only one side is ever held), so phase-15's
"×2 on flip" cap gap closes automatically — no code change needed.

The `_certainty` helper stays (the cap formula uses it). Only `_certainty_size` is removed
(replaced by `flat_size`).

---

## 2. Backtest (gate before paper)

- Re-run the real `compute_ladder` (invariant: not a copy) over the cached resolved markets
  with phase-16 defaults; compare to the recorded live baseline (−$282.53) and report.
- Sweep to confirm the profitable region is robust, not a single lucky point:
  - `favorite_min_price` ∈ {0.80, 0.85, 0.90}
  - `entry_start_frac` ∈ {0.50, 0.60, 0.70}
  (9-cell grid; print PnL/worst per cell.) Keep `max_entry_price` swept as today.
- Report in `reports/` with the grid + honest caveats (coarse fill model; cap not modelled
  in backtest; small sample; backtest is a filter, paper is the real test).

---

## 3. Testing

Unit tests (`tests/test_ladder.py`, updated for new thresholds — note `LATE_TTE` and test
mids must satisfy the new gates: window_frac ≥ 0.60 and favorite price ≥ 0.85):
| Test | Asserts |
|---|---|
| `test_picks_higher_side_as_favorite` | mid 0.90 → YES bids; mid 0.10 → NO bids |
| `test_below_min_price_no_quotes` | favorite 0.80 (< 0.85) → `[]` |
| `test_too_early_no_quotes` | window_frac < 0.60 → `[]` |
| `test_caps_at_max_entry_price` | no bid price > 0.97 |
| `test_commit_one_side_holds_yes` | inventory_yes_qty>0 + NO-favorite mid → `[]` |
| `test_commit_one_side_holds_no` | inventory_no_qty>0 + YES-favorite mid → `[]` |
| `test_flat_size` | every bid size == `flat_size`, independent of price/window_frac |
| `test_falling_favorite_suppressed` | favorite price fell vs prev → `[]` (unchanged) |
| `test_velocity_*` | unchanged velocity-gate behavior |
| `test_only_favorite_side` | all bids one side |

Backtest test (`tests/test_backtest_engine.py`): update the profitable-favorite scenario so
the rising favorite is in the ≥0.85 band during the last 40% of the window (so phase-16
gates pass and it fills).

Full suite must be green.

---

## 4. Success criteria

1. **Backtest:** phase-16 beats phase-15 defaults on the cached sample on BOTH total PnL and
   worst-case (tail), and the worst single-market loss shrinks materially (the −$46/−$60-class
   tail should not appear, because commit-one-side + flat size + late/high entry remove the
   accumulation-and-flip mechanism).
2. **Robustness:** the advantage holds across most of the 9-cell sweep, not one cell.
3. **Paper is the real test:** after merge, run phase-16 in paper and observe whether the
   −$40+ single-window tail is gone and net trends positive.

**Honest framing (unchanged from phase-15):** the backtest is a filter against the
obviously-bad. Real profitability is unproven until paper (and ultimately live with proper
co-location). This phase makes the tactic *correct* (match the competitor); it does not by
itself prove profitability.

---

## 5. Out of scope
- Live trading mode (explicit user constraint — paper only).
- Dashboard live-editable config (separate future phase "A").
- Changing the per-market cap value/mechanism (user keeps it to tune live later).
- Fill-rate / latency (to be solved by AWS deployment later).

## 6. Sources
- Competitor profile: `data-api.polymarket.com/activity` for
  `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30` (2503 rolling trades, dollar-weighted).
- Our paper data: `state.db` (phase-15 run, 50+ resolved 5m windows).
- Prior: `docs/superpowers/specs/2026-05-30-phase15-late-favorite-design.md`.

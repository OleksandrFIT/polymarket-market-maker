# Binance Trend Detector — Design

**Date:** 2026-06-11
**Status:** approved (brainstorm), pending implementation plan

## Goal

Stop the laddered re-quoter from accumulating the losing side on trending windows
(the −EV naked legs). A pure probabilistic detector, fed real-time BTC price from
Binance, estimates which side is winning; when the losing side's win-probability
drops below a confidence threshold, `plan_ladder` suppresses that side's rungs (we
sit out the trend instead of buying the crasher). This converts the strategy from
"marginal + luck-dependent on trends" to "reliably positive" (model: +$3-8/hr → a
stable ~+$7-8/hr, by removing the trend gamble).

## Background / evidence

- Competitor analysis ([[project-competitor-verdict]]): the laddered cheap-pair edge
  is real (+$2,826), but directional/naked legs LOSE (−$2,226). On trends we
  structurally buy the crashing loser; you cannot profit on a trend, only minimize.
- Ladder sim (this session): without trend handling, the 12-window series EV swings
  +$3.10 → +$7.60 depending on the unknown trend-win-rate (a gamble). WITH the
  detector (cap=10, ~90% accuracy), EV is +$7.7 → +$8.2 — nearly flat, because we
  sit out trends so their win-rate stops mattering. The detector COLLAPSES the
  variance, not just raises the mean.
- Scope: **detector only** (v1). Taker-completion of naked legs is DEFERRED — analysis
  showed it barely changes EV given the detector (~17% residual naked legs are cheap
  coin-flips ≈ neutral); it only smooths variance. Add later if desired.

## Resolution mechanics (verified, foundational)

A 5m market resolves **"Up" if BTC at the END of the window ≥ BTC at the START**
("strike"), else "Down". The resolution source is the **Chainlink BTC/USD** data
stream — explicitly *not* Binance/spot. We use **Binance spot as a proxy** for the
detector because (a) we already have the live feed, and (b) the detector only acts on
CLEAR moves (confidence threshold), where Binance and Chainlink agree; their basis
only matters near the strike, where the detector stays NEUTRAL anyway. Using the
Chainlink stream directly (exact strike) is a possible v2 refinement, not needed for
trend-detection.

## Architecture

```
quoter/runner/trend_detector.py   NEW, pure (no I/O): win_prob_up(), detect_bias()
quoter/feeds/binance_ws.py        EXISTS. Runner runs a BinanceWS + rolling price buffer
quoter/runner/merge_runner.py     _ladder_window: capture strike at entry, compute bias each tick
quoter/runner/ladder_planner.py   plan_ladder: + trend_bias param → suppress loser's rungs
quoter/config.py                  + trend knobs
tests/test_trend_detector.py      NEW: pure model tests
```

The pure `trend_detector` is the brain; `BinanceWS` (existing) is the eyes; the rest is
wiring. Reuses the existing rung-suppression mechanism in `plan_ladder` (empty desired
list → cancel that side's rungs, identical to the naked-cap path).

## Core: probabilistic model (pure)

```
win_prob_up(price_now, strike, sigma_remaining) -> float
detect_bias(price_now, strike, sigma_remaining, cfg) -> "UP" | "DOWN" | "NEUTRAL"
```

1. `sigma_remaining` = expected $-spread of BTC over the remaining time =
   `dollar_vol_per_sec * sqrt(time_left)`. `dollar_vol_per_sec` is estimated from the
   recent-price buffer (stdev of price moves per second); if the buffer is too thin,
   use `cfg.trend_vol_fallback * sqrt(time_left) / sqrt(window_len)` as a floor. A small
   positive floor on `sigma_remaining` prevents division blow-up as `time_left → 0`.
2. `p_up = Φ((price_now - strike) / sigma_remaining)` (Φ = standard normal CDF,
   computed via `math.erf`: `0.5 * (1 + erf(x / sqrt(2)))`).
3. Decision with `t = cfg.trend_confidence` (default 0.35):
   - `p_up < t`       → Up is the near-certain loser → **bias = "DOWN"** (suppress YES rungs).
   - `p_up > 1 - t`   → Down is the near-certain loser → **bias = "UP"** (suppress NO rungs).
   - otherwise        → **"NEUTRAL"** (full ladder).

**One knob + automatic behavior:** `sqrt(time_left)` makes the detector harden over the
window (same gap = more confidence late); buffer-estimated vol auto-widens the bar on
volatile markets (no suppressing on normal noise); the single tunable is `trend_confidence`.

**Dynamic, not latching:** recomputed every tick. If the trend reverts (`p_up` returns to
the 0.35–0.65 band), bias goes back to NEUTRAL and the suppressed side re-opens. Already-
caught inventory is held to resolution (never sold).

## Live wiring (`_ladder_window`)

- Runner starts `BinanceWS(("BTC",), on_price)` as a background task alongside `run_forever`;
  `on_price(asset, price, ts)` appends `(price, ts)` to a rolling buffer, dropping entries
  older than `cfg.trend_buffer_sec`.
- On window entry: `strike = ` latest buffered BTC price (Chainlink-open proxy).
- Each tick: compute `dollar_vol_per_sec` + `sigma_remaining` from the buffer and
  `m.time_remaining()`, call `detect_bias`, pass the result as `trend_bias` to `plan_ladder`.
- **Fail-safe:** if the buffer's newest entry is older than `cfg.trend_stale_sec` (Binance
  silent/disconnected) OR `cfg.trend_enabled` is False → bias forced to `"NEUTRAL"` (trade
  as if no detector — never worse than the current ladder).

## `plan_ladder` change (minimal)

Add `trend_bias: str = "NEUTRAL"`. After computing `desired` rungs, before the diff:
```
trend_bias == "UP"   → desired["NO"]  = []   # suppress Down (loser)
trend_bias == "DOWN" → desired["YES"] = []   # suppress Up (loser)
```
Empty desired → the existing diff cancels that side's resting rungs. No new cancel logic.
Default `"NEUTRAL"` keeps all existing callers/tests unchanged.

## Config additions

```python
trend_enabled: bool = True
trend_confidence: float = 0.35    # THE knob: suppress a side when its win-prob < this
trend_buffer_sec: float = 60.0    # rolling price-buffer window
trend_vol_fallback: float = 30.0  # fallback $-vol of BTC over a 5m window if buffer thin
trend_stale_sec: float = 10.0     # buffer older than this → NEUTRAL (fail-safe)
```

## Error / edge handling

- **Binance silent / buffer stale (> trend_stale_sec)** → NEUTRAL (no signal → no suppression).
- **`time_left → 0`** → `sqrt(time_left)` small → confidence rises (correct); `sigma_remaining`
  floored to a small positive value to avoid division blow-up.
- **Thin buffer (just entered)** → use `trend_vol_fallback`; until a strike is captured, NEUTRAL.
- **Trend reversal** → recomputed each tick → NEUTRAL → suppressed side re-opens.
- **Detector only suppresses NEW rungs**; never sells already-caught inventory (held to resolution).

## Testing (pure `trend_detector`)

- BTC clearly above strike, little time left → bias `"UP"` (suppress Down).
- Clearly below strike → `"DOWN"`.
- Near strike → `"NEUTRAL"`.
- Time-decay: same gap with lots of time → NEUTRAL; with little time → fires.
- Volatility: large `sigma_remaining` → same gap does NOT fire (bar auto-widened).
- Reversal: `p_up` back in band → NEUTRAL.
- `win_prob_up` sanity: price_now == strike → 0.5; far above → ~1.0; far below → ~0.0.
- Fail-safe (wiring-level, lightweight): stale buffer → NEUTRAL.

## Out of scope (v1)

- Taker-completion of residual naked legs (deferred; cheap coin-flips, ~neutral EV).
- Chainlink stream integration for the exact strike (Binance proxy suffices for clear trends).
- Leaning INTO the winning side (you cannot profit on a trend — detector is purely defensive).

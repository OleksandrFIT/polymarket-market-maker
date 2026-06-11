# Trend Detector — Time Gate Fix

**Date:** 2026-06-12
**Status:** approved (brainstorm), pending implementation plan
**Extends:** `docs/superpowers/specs/2026-06-11-trend-detector-design.md`

## Problem

The trend detector decides "is it a trend?" from the **instantaneous** BTC price vs the
window strike. In a normal choppy window BTC swings ±$25 constantly (noise); each swing
momentarily looks like a mini-trend, so the detector fires and suppresses the side that is
*currently cheapening* — exactly the side we want to catch. It therefore sabotages the
profitable choppy windows.

Measured in the end-to-end simulation (this session): a choppy window goes from **+$2.00**
(no detector) to **−$3.85** (current detector). Net, the detector as-is is harmful, not
helpful (sim total −$13.00 vs −$14.70 with no detector — barely better, and it breaks the
windows we win). Not dangerous (risk stays bounded) — just self-sabotaging.

## Fix — time gate

The detector acts **only in the last `trend_gate_sec` seconds** of the window:
- `time_left > trend_gate_sec` → always **NEUTRAL** (early swings are ignored as noise that
  will likely revert).
- `time_left <= trend_gate_sec` → apply the existing probability rule (late in the window
  there is no time to reverse, so a clear position is a real trend → act on it).

This is the time-decay principle made explicit and aggressive: don't trust a directional
read until reversal is implausible. Single new knob: `trend_gate_sec` (default 90.0).

## Why time-gate (not debounce)

Simulated all three fixes (sum over 5 scenarios, pessimistic deterministic winners):

| fix              | choppy | quiet | dip   | trend | total  |
|------------------|--------|-------|-------|-------|--------|
| none (off)       | +2.00  | −3.55 | −3.65 | −4.75×2 | −14.70 |
| raw (current)    | −3.85  | −2.45 | +2.80 | −4.75×2 | −13.00 |
| debounce only    | +2.00  | −2.35 | −1.65 | −4.75×2 | −11.50 |
| **time-gate 90** | +2.00  | +0.90 | +2.80 | −4.75×2 | **−3.80** |
| both             | +2.00  | +0.90 | −1.65 | −4.75×2 | −8.25  |

Time-gate is clearly best: preserves choppy, helps quiet/dip, doesn't hurt anything.
Debounce alone fixes choppy but breaks dip; combining the two re-breaks dip. Strong trends
are unchanged by any fix (the `naked_cap=10` already pulls the side before the detector
would). YAGNI → time-gate only, one knob.

## Changes

- `quoter/config.py`: add `trend_gate_sec: float = 90.0`.
- `quoter/runner/trend_detector.py`: `detect_bias` gains a `time_left: float` param; if
  `time_left > cfg.trend_gate_sec` → return `"NEUTRAL"` before the probability rule.
- `quoter/runner/merge_runner.py`: `_trend_bias` passes `time_left` to `detect_bias`
  (it already computes `time_left` for `sigma_remaining`).

## Testing

- `detect_bias` with `time_left > trend_gate_sec` → "NEUTRAL" even for a clearly-trending
  price (gate blocks early action).
- `detect_bias` with `time_left <= trend_gate_sec` and a clear trend → fires ("UP"/"DOWN")
  as before.
- Existing `detect_bias` tests updated to pass a small `time_left` (within the gate) so they
  still exercise the probability rule.

## Honest note (carried from prior analysis)

The gate makes the detector *helpful instead of harmful*, but the strategy's overall
profitability is still trend-mix-dependent and only a small live run can measure it. This
fix makes the bot "correct"; it does not by itself prove profitability.

## Out of scope

- Debounce / persistence-count filter (tested worse than the gate).
- Any change to the ladder, naked-cap, or taker-completion (still deferred).

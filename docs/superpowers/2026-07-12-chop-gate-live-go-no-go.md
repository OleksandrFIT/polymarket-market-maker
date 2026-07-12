# Chop-Gate Live Measurement — PRE-REGISTERED Go/No-Go (2026-07-12)

**Pre-committed BEFORE any live data exists.** Purpose: turn the live result into an automatic
decision, not a judgment call made after hours of watching a P&L tick. The temptation after a live
run is always "just a bit more data" — this file removes that choice by fixing the criterion in
advance (and in git history, timestamped/immutable). Same honesty gate already built into the
offline sim (`_pairquality.py` verdict), now applied to myself.

## The one metric

`effective_pair_cost` = rebate-adjusted realized pair cost =
`pair_cost − rebate_accrued / pairs_merged`, read from the `topbook_fillquality` telemetry event
(field `pair_cost_effective`). This already accounts for the maker rebate (the 2nd revenue stream,
crypto_fees_v2), so break-even is ~$1.00 on this metric, not below it.

## Population (STRICT — do not widen after the fact)

- **Causal-chop windows ONLY:** windows the detector acted on as chop — `detector == "chop"`
  in `topbook_fillquality` (i.e. NOT revoked to CLOSING by trend). Revoked/trend windows are
  EXCLUDED from the go/no-go metric; they are the windows the tactic deliberately skips
  accumulating in, so their pair economics do not test the chop hypothesis.
- **Minimum sample: n ≥ 50 merged pairs** across those causal-chop windows. Below 50 pairs the
  result is NOT eligible for a decision — keep running or stop, but do NOT read a verdict into it.
  (n is pairs, not windows: a window contributes its `pairs_merged`.)

## Decision bands on mean `effective_pair_cost` (causal-chop, n ≥ 50)

| Band | Mean effective_pair_cost | Verdict | Action |
|------|--------------------------|---------|--------|
| **GO** | **≤ 0.96** | Edge is real live (≥ 4¢/pair after rebate) | Chop-gated maker-both validated. Proceed to scale discussion (size/capital). |
| **AMBIGUOUS** | **0.96 – 0.995** | Thin / inconclusive | Do NOT scale. Either gather more pre-registered n or treat as ceiling-confirmed. No new tactic variants off this result. |
| **NO-GO** | **> 0.995** | Tactic does not deliver cheap pairs live | Ceiling confirmed. Stop the chop-gate live branch. Do not re-run at this size hoping for a better draw. |

## Rules that make this a pre-commitment (binding on me)

1. **No peeking-to-stop.** Do not end the run early because the number "looks good/bad" before
   n ≥ 50. The stop condition is n ≥ 50 pairs OR the operator's manual stop OR the watchdog — not
   the metric's momentary value.
2. **No post-hoc population changes.** The metric is causal-chop windows, effective_pair_cost. Not
   "chop after excluding window X", not "all windows if chop looks bad", not switching to raw
   pair_cost because effective looks worse. If a genuinely new question arises, it is a SEPARATE
   pre-registered run, not a re-read of this one.
3. **The bands are the decision.** ≤0.96 → GO. 0.96–0.995 → don't scale. >0.995 → stop. Whatever
   the number lands on, the row it falls in IS the action. No narrative override.
4. **Secondary telemetry is diagnostic, not decisional:** the causal-vs-hindsight confusion matrix
   (`detector` vs `hindsight`), `cap_replaces`/`shift_replaces`, naked resid outcomes — these
   explain WHY a band was hit and feed the next design, but they do not move the go/no-go line.

## Provenance

- Grid-search on recorded tapes picks the config (thresholds) BEFORE this live run; it does not
  set the go/no-go line (shadow-fill is optimistic in absolute — it ranks configs, it does not
  predict the live absolute pair cost this file judges).
- Live run is gated: AWS server only, explicit per-instance "go", watchdog dd-stop armed.

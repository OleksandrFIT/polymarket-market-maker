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

## Stopping rule (TWO-SIDED — both directions pre-committed)

"No peeking to stop early" (rule 1) guards against ending the run the moment the number looks
good/bad. But a one-sided lower bound (n ≥ 50) turns a slow market into an open-ended test — and an
open end is itself the softest form of peeking (keep going until it "works"). So the stop is bounded
on BOTH sides:

- **Lower bound:** do not read a verdict before **n ≥ 50 merged pairs** (causal-chop). Below that,
  no GO/NO-GO.
- **Upper bound:** the run ends at **48 hours wall-clock OR the watchdog dd-stop, whichever comes
  first** — the dd-stop is **equity ≤ −$10** persisting **2 consecutive reads** (`deploy/live_watch.sh`
  `LIMIT=-10.0`, `DD_NEEDED=2`), with an instant **naked > 8** tripwire (`NAKED_LIMIT=8` = cap 6 + 2).
  These are PRE-REGISTERED here so the dd number is not chosen mid-run (that would be the discretion
  the stopping rule exists to remove). If the run ends with n < 50, the result is **"INSUFFICIENT n"**
  — explicitly NOT a GO/NO-GO read, and NOT a licence to extend the run to collect quorum. A new
  decision (run longer, change size, stop) is made deliberately, not by drift.

Sizing sanity: at ~2-3 pairs per traded chop window and ~50% chop share, n = 50 accrues in roughly
**8-12 hours** of normal market. The 48h ceiling leaves generous margin; hitting it with n < 50
means the market was abnormally thin, which is information, not a metric to chase.

## Monitoring vs peeking (operational — the line, drawn before launch)

"No peeking to stop before n≥50" is about the DECISION, not about watching. The two are different
and must not be conflated:

- **MANDATORY (operational monitoring):** errors/tracebacks, watchdog state, dd-stop, that orders
  actually place AND cancel, that fills credit, that telemetry writes, naked never exceeds cap. Watch
  this actively, especially the first hours — this is the FIRST time the `cap_override`, `completion`
  (FOK-buy light leg) and `sell-loser` (FOK-sell heavy) branches execute against REAL fills, a class
  of code the dry-run structurally could not exercise (no fills → no inventory → those paths never
  ran). A watchdog dd-stop is a LEGITIMATE stop under the pre-registered rule.
- **WATCH `topbook_fill_assumed` specifically.** Maker fills are credited from the exchange-authoritative
  `GET /order/{id}.size_matched` (a vanished order that was cancelled/rejected/expired reports
  `size_matched=0` → no phantom credit). The ONE soft spot is the fallback: when that lookup itself
  fails (returns None), the code assumes a FULL fill (`matched=sz`) and logs `topbook_fill_assumed` —
  fail-conservative for spend, but a phantom for `pair_cost`. This fires only on order-status lookup
  failure, concentrated in the cancel/replace-heavy first hours. If it fires more than a handful of
  times, PAUSE and investigate — repeated assumes corrupt the decision metric. (This is an operational
  stop, not peeking.)
- **FORBIDDEN (peeking):** reading the intermediate `pair_eff` and deciding to stop/continue on it.
  If the hand reaches for STOP because "the first 20 pairs look bad" — that is peeking. The metric is
  read ONCE, at n≥50 (or at the 48h/dd upper bound → "insufficient n"). Operational failures stop the
  run; the metric's momentary value never does.

## Expectation anchor (set from the wider sample, NOT the favourable slice)

The grid's `window_record.pnl` (raw prices, **no rebate**) is **+$0.35/window** over 2,523 windows,
down from +$0.54 on the narrower favourable 4-9 Jul slice (~35% regime-mix haircut). But the decomposition
(`scripts/_pnl_split.py`) shows the **rebate is a real +$0.20/window** cash stream the raw-price pnl omits →
**economic expectation ≈ +$0.55/window (shadow)**, ~×0.5 live ≈ **+$0.27/window**. Anchor to those, not to
the old +$0.54. Decomposition (shadow, $/window): merge-gross **+$1.58** + rebate **+$0.20** = gross **+$1.78**;
naked residual **−$1.23** → net **+$0.55**. **The naked leg eats 69% of the gross** (merge:naked = 1.28:1) and
loses **79% of naked windows** (naked shares lost:won = 4.1:1) — the tactic is a thin **+31% residual** after
pairs and naked near-cancel, so live execution slippage hits it with ~3× leverage (why the live measurement is
load-bearing, not optional). Per-day distribution will contain near-zero / negative days (normal, pre-known).
The anchor is a PnL statement; it does NOT move the go/no-go line, which is on pair_eff (already rebate-adjusted).

## Pre-flight checklist (before the entry dry-run)

1. **Deploy the FULL commit chain** (through the current HEAD that passed the suite) to the server —
   a fresh checkout/pull, NOT a `git archive` of individual files. Live must run byte-for-byte the
   code the 501-test suite validated. (The `git archive` of research scripts done for the offline
   grid was fine because it was read-only; the live path must not be assembled piecemeal.)
2. **In the dry-run, read three numbers** (plumbing, not EV):
   - **Revocation fraction** vs the ~20% trend baseline — if far off, the detector is mis-firing.
   - **`revoked_at_sec` distribution** — clustering at 110-115s means the sliding detector is
     effectively acting only at its earliest allowed tick (not truly sliding); a healthy spread
     across the window is expected.
   - **`cap_replaces` vs `shift_replaces`** — both should be non-zero over a dry-run; an all-zero
     axis means that requote path never exercised (wiring gap).
3. **`LIVE_GO` stays OFF** until the explicit per-instance "go". Dry-run is `chop_gate=1` with no
   `LIVE_GO`.

## Provenance

- Grid-search on recorded tapes picks the config (thresholds) BEFORE this live run; it does not
  set the go/no-go line (shadow-fill is optimistic in absolute — it ranks configs, it does not
  predict the live absolute pair cost this file judges). Day-slice confirms the config choice is
  below day-to-day noise (see `scripts/_day_slice.py`), so "center defaults 0.02/45/60" needs no
  gaming argument — the grid simply did not distinguish.
- Live run is gated: AWS server only, explicit per-instance "go", watchdog dd-stop armed.
- **Config = sim config (pre-registration integrity).** The offline sims that set these thresholds
  (grid / day-slice / calib, `top_book_window_gated`) all ran **naked_cap 6, size 5, no early-aggressive
  phase, near-end-only completion**. The live path (`run_control` REGIME_GATE=0) was aligned to exactly
  that (commit `5bb0686`) — it is NOT the legacy 0xb27b-neutral bundle (cap 12 / early-10 /
  continuous-complete). If any of these change, the thresholds must be re-derived. cap 6 keeps the
  worst single window near −$3 (not the −$5 a cap-12 tail would give) and matches watchdog `NAKED_LIMIT=8`.

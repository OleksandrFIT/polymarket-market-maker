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
- **Minimum sample: n ≥ 80 causal-chop WINDOWS** (unit is windows, not pairs — see the stopping-rule
  lower bound for the σ/SE derivation). Below 80 windows the
  result is NOT eligible for a decision — keep running or stop, but do NOT read a verdict into it.

## Decision bands on mean `effective_pair_cost` (causal-chop, n ≥ 80 windows)

| Band | Mean effective_pair_cost | Verdict | Action |
|------|--------------------------|---------|--------|
| **GO** | **≤ 0.96** | Edge is real live (≥ 4¢/pair after rebate) | Chop-gated maker-both validated. Proceed to scale discussion (size/capital). |
| **AMBIGUOUS** | **0.96 – 0.995** | Thin / inconclusive | Do NOT scale. Either gather more pre-registered n or treat as ceiling-confirmed. No new tactic variants off this result. |
| **NO-GO** | **> 0.995** | Tactic does not deliver cheap pairs live | Ceiling confirmed. Stop the chop-gate live branch. Do not re-run at this size hoping for a better draw. |

**Margin note (production-faithful, added after the `hard_cap` calib + per-regime pair_eff).** The
production-faithful sim (same skew as live) puts pair_eff BY REGIME at: **chop 0.915** / reversal 0.920 /
trend 0.936 — NOT the looser sim's 0.89. The go/no-go is measured on causal-CHOP windows, so the relevant
shadow number is **chop pair_eff ≈ 0.915**, leaving only **~4.5¢** to the 0.96 GO line as the live-degradation
budget (queue + adverse selection). The 0.89 figure was a looser-sim artifact: it allowed over-accumulation
(naked to ~11) and the cheapest pairs come from filling one leg deep on a late dump — production `skew_ok`
caps that at 6, so the bot skips those cheap late fills and the pair costs ~0.915. **This is material: the
"cheap 0.89 pair, comfortable 7¢ margin" story was never reachable by the code that will run; the honest
picture is "0.915 pair, thin 4.5¢ margin," so the prior probability of a live GO is LOWER — the measurement
is now more likely to return NO-GO/AMBIGUOUS than GO.** The 0.96 line stays FIXED (pre-registered; goalposts
do not move) and is measured live directly. Provenance is closed with the live skew semantics: clock-only
wins the hard_cap calib (+$0.326/win, gap to best detector widened to $0.0065/win); soft revoke is −EV.

## Rules that make this a pre-commitment (binding on me)

1. **No peeking-to-stop.** Do not end the run early because the number "looks good/bad" before
   n ≥ 80 windows. The stop condition is n ≥ 80 causal-chop windows OR the operator's manual stop OR the watchdog — not
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
good/bad. But a one-sided lower bound (n ≥ 80 windows) turns a slow market into an open-ended test — and an
open end is itself the softest form of peeking (keep going until it "works"). So the stop is bounded
on BOTH sides:

- **Lower bound:** do not read a verdict before **n ≥ 80 causal-chop WINDOWS** (unit corrected from
  "pairs" after the case-audit). The metric's variance lives at the WINDOW level, not the pair level —
  pairs within a window share the same market draw, so the effective sample size is independent windows,
  not pairs. Measured per-window pair_eff σ (chop subset) = **0.095**, so SE of the mean = σ/√(windows):
  n=50 → 1.35¢, n=80 → 1.06¢, n=100 → 0.95¢. The HOLD band (0.96–0.995) is only 3.5¢ wide, so SE must be
  ≤~1¢ to resolve HOLD from STOP → **n ≥ 80 windows** (≈1 day at ~100+ chop windows/day; the old "8–12h"
  estimate matched WINDOWS, not the mis-stated "2–3 pairs/window"). Below n=80 windows, no GO/NO-GO.
- **Upper bound:** the run ends at **48 hours wall-clock OR the watchdog dd-stop, whichever comes
  first** — the dd-stop is **equity ≤ −$10** persisting **2 consecutive reads** (`deploy/live_watch.sh`
  `LIMIT=-10.0`, `DD_NEEDED=2`), with an instant **naked > 8** tripwire (`NAKED_LIMIT=8` = cap 6 + 2).
  These are PRE-REGISTERED here so the dd number is not chosen mid-run (that would be the discretion
  the stopping rule exists to remove). If the run ends with n < 80 windows, the result is **"INSUFFICIENT n"**
  — explicitly NOT a GO/NO-GO read, and NOT a licence to extend the run to collect quorum. A new
  decision (run longer, change size, stop) is made deliberately, not by drift.

Sizing sanity: the entry gate is nearly a no-op (windows open at the strike ~0.50 → only ~1% fail the
balanced filter, per the case-audit), so ~100+ causal-chop windows accrue per day; **n = 80 windows ≈ 1 day**
of normal market. The 48h ceiling leaves margin; hitting it with n < 80 means the market was abnormally thin,
which is information, not a metric to chase. (The earlier "2-3 pairs/window, 8-12h" wording was wrong on the
unit — the case-audit measured ~17 pairs/window and the binding sample size is windows, not pairs.)

## Monitoring vs peeking (operational — the line, drawn before launch)

"No peeking to stop before n≥80 windows" is about the DECISION, not about watching. The two are different
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

**Production-faithful numbers** (`scripts/_regime_split.py`, `hard_cap=True` = naked capped at 6 as the live
`skew_ok` does; the calib/grid used a looser pre-fill skew that let naked overshoot to ~cap+size and OVERSTATED
the tail). 2,523 windows, WITH rebate, $/window:

| regime | share | net $/win | %neg | worst win | naked lost |
|---|---|---|---|---|---|
| chop | 52% | **+0.95** | 25% | −3.20 | 78% |
| reversal | 26% | **+0.17** | 44% | −3.57 | 92% |
| trend | 21% | **−0.19** | 51% | −2.97 | 96% |
| **all** | | **+0.50** | 36% | **−3.57** | |

**NOT uniform:** chop carries it, trend loses (mildly), reversal ≈ flat. Distribution ($/win, with rebate):
p05 −2.23 / p25 −0.53 / p50 +0.57 / p75 +1.45 / p95 +3.23; min −3.57, max +8.12. **Anchor: +$0.50/window shadow,
×~0.5 live ≈ +$0.25.** Worst single window ~−$3.5 (bounded by cap 6). The naked leg loses 78–96% of the time by
regime — the structural risk; cap 6 keeps it small. This is a PnL statement; it does NOT move the go/no-go line
(on pair_eff, live-measured, sim-independent — the skew-fidelity gap changes the risk numbers, not the metric or
the clock-only choice). Per-day distribution has near-zero / negative days (normal, pre-known). The earlier +$0.55
figure came from the looser sim (which also overstated the tail as −$7); +$0.50 with a −$3.5 tail is the honest one.

## Trend-detection ceiling — the detector branch is permanently closed

Two independent measures bound what ANY trend-revocation could ever add, and both say "negligible":
- **Upper bound from the per-regime table:** a *perfect* detector that skipped every trend window would
  save the trend loss entirely: `0.21 (trend share) × $0.19 (trend loss/window) ≈ +$0.04/window`. That
  is the absolute ceiling — a real detector, with false positives, gets less.
- **Measured from the calib:** best detector config vs clock-only differed by **$0.0055/window**.

Both land on the same scale: **after cap 6, trends are a small bounded cost that no detection complexity
can pay for.** The earlier (looser-skew) calib actually ran *in the detector's favour* — it overstated the
naked drag the detector was meant to cut, and the detector STILL lost to clock-only. The fix only widened
that margin. Do not revisit trend detection at this size/config; the observe-only `would_revoke_at_sec`
telemetry already logs, for free, whether live ever contradicts this. This ceiling closes the branch.

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

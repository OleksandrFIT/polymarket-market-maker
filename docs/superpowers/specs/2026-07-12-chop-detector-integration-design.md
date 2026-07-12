# Chop-Detector Integration (revocable gate + cancel/replace policy) — Design Spec

**Goal:** Wire the chop-tactic into the live `top_book` bot BEFORE the one live measurement, so the
live test measures the NEW tactic (not the old top_book gate). Three coupled pieces, because each
changes both pair_cost and fill-ratio — the exact quantities live will measure: (1) a **revocable
CLOSING gate** driven by a *sliding* chop-detector, (2) a **directional cancel/replace policy** with
dwell, (3) **causal+hindsight telemetry** for a free live confusion matrix. Thresholds are picked by
an offline grid-search on the 1,256 recorded tapes, not guessed.

**Status:** Changes to the production `_top_book_window` (merge_runner) + new pure helpers + config.
Live-locked behind `LIVE_GO=1` as today; default dry-run. Followed by a plumbing-regression dry-run
(NOT an EV run).

---

## Background — why revocable, and the bug being fixed

The detector needs ~100s of price path to classify, but entry must be early to catch fills. So the
live form of the detector is **revocation**: enter and make from t~0; from t≥100s the detector may
flip the window to CLOSING (stop new accumulation, finish existing pairs). This bounds the naked leg
in a late-detected trend.

**Bug fixed (critical):** a cumulative "crossed 0.5 ever ⇒ chop forever" certificate NEVER revokes a
**mid-trend** — the exact class it exists for. Mid-trends hang near 0.5 (crossing 0.5 several times)
then commit late; under a cumulative rule they carry a permanent chop stamp and pass the gate. The
revocation condition must be **sliding** (trailing tail), not cumulative.

## Architecture — units

### Unit 1 — `chop_revoke(mid_hist, now, dev_thresh, lookback_sec) -> bool` (pure)

Sliding revocation signal on the Up-mid history. Returns True (this window is now committing to a
trend) iff, at `now`:
- `|mid(now) − 0.5| ≥ dev_thresh` (currently committed), AND
- **no 0.5-crossing in `[now − lookback_sec, now]`** (stopped oscillating — the tail is one-sided).

The caller (state machine) adds the **confirm** and **one-way** logic (below). Cumulative features
(`max|dev|` over all history, `crossed ever`) are NOT used for revocation — only, if desired, as
context in the initial evaluation. Pure, unit-tested. Lives in `quoter/runner/top_book_planner.py`
(or a small `chop_gate.py`).

### Unit 2 — CLOSING state machine in `_top_book_window` (unified trigger)

One boolean `closing`, one-way per window (ACCUMULATING → CLOSING, never back — re-entry at 150s+
buys too little pairing time and adds a bug class). CLOSING is triggered by **either**:
- **detector**: from t≥`chop_detect_sec`, `chop_revoke(...)` holds continuously for ≥`confirm_sec`
  (anti-flip), OR
- **clock**: `time_remaining ≤ freeze_sec`.

Both collapse to the SAME code path (this is the fix for point 3: `freeze` is NOT freeze-in-place — a
resting accumulation bid in the last 20-30s is a fill that can't be paired and catches the final snap
that resolves to 0). In CLOSING:
- **cancel all accumulation bids**; place NO new accumulation quotes,
- keep only: **complete** the light leg (taker, if pair < $1 effective), **merge**, **sell** the loser
  (if pair ≥ $1) — i.e., completion-only on the naked legs we already hold,
- record `revoked_at_sec` (the tick that first entered CLOSING) and `closing_reason` ("trend"/"clock").

ACCUMULATING (before CLOSING): quote both sides best+tick, linked-pair capped, via Unit 3's policy.

### Unit 3 — `plan_requote(resting, target, last_replace, now, replace_shift, dwell_sec) -> (cancel, post)` (pure)

Directional, dwell-bounded cancel/replace, replacing the symmetric `diff_quotes` for accumulation.
Asymmetry (point 2): a mid move DOWN to our bid is our PLAN (cheap fill on the dump) — do NOT chase
down; a mid move UP away from our bid makes the bid DEAD (won't fill) — pulling up trades queue
position for fill-rate (the quantity we measure). Rules, per side:
- **CAP-OVERRIDE (invariant, runs FIRST, ignores direction AND dwell):** if the resting bid sits
  ABOVE the current linked-pair ceiling for its side (`resting.price > cap[side]`), always cancel +
  repost down to the capped target. A `target.price` can drop for two reasons — the mid fell (our
  plan, keep) OR the cap tightened because `heavy_avg` grew (a stale bid above the cap that, if
  filled, assembles a pair **≥ $1**). `plan_requote` cannot tell them apart from price alone, so the
  cap is passed as a separate argument. This maintains the "pair < $1 by construction" guarantee — and
  it matters most in trends, where `heavy_avg` grows and violations would concentrate exactly where
  the naked leg is already dearest.
- **Pull UP only:** replace iff `target.price ≥ resting.price + replace_shift` and dwell elapsed.
- **Never chase down:** if `target.price < resting.price` (mid fell to us), keep the resting bid (fills
  on the dump, or CLOSING cancels it).
- **Within `replace_shift` / dwell not elapsed:** no replace (anti-churn). New posts (no resting) are
  unconditional; `last_replace[side]` is stamped on ANY post (initial + repost), else the first repost
  churns with no dwell.

Pure, unit-tested; returns `(cancel, post, cap_sides)`. `link_pair_bids` still caps the light side
before this runs. **Compromise (acknowledged):** in a trend the cap-override reprices the light leg
down repeatedly — queue loss there is inevitable and acceptable (a trend is heading to revocation
anyway). Telemetry MUST count `cap_replaces` separately from `shift_replaces`, else the two mix in
live logs and corrupt the fill-rate interpretation.

### Unit 4 — Telemetry: causal + hindsight (cheap now, decisive after live)

`topbook_fillquality` already carries `pair_cost_effective` + `rebate_accrued` (added 2026-07-11).
Add:
- `detector` — the causal label the bot acted on: "chop" (never revoked) or "revoked" +
  `revoked_at_sec` + `closing_reason`.
- `hindsight` — post-hoc regime from the FULL path (chop/reversal/trend) via the same classifier the
  offline sim uses (computed at window end from the mid_hist we already keep).

Logging both gives a **live confusion matrix for free**, and after the live test lets us split PnL
into "detector was wrong" (causal ≠ hindsight) vs "fills worse than shadow" (causal right, still
lost) — two failures with different fixes.

### Unit 5 — Offline threshold grid-search (before finalizing config)

`scripts/_config_grid.py`: run the maker-both sim (reusing `_chop_detector_sim` machinery + the new
`chop_revoke`/`plan_requote`) over the 1,256 tapes for the grid
`replace_shift ∈ {1,2,3 ticks} × freeze_sec ∈ {30,45,60} × lookback_sec ∈ {40,60,80}`, rank by
shadow pair_cost_effective and PnL. Shadow-fill is optimistic in absolute but ranks configs' relative
order adequately — enough to start live from a chosen point, not a random one. Center the grid on the
starting defaults (0.02 / 45 / 60). Half a day of compute, zero risk. Output feeds the final config.

## Config knobs (new; starting defaults, grid-search confirms)

```
chop_detect_sec   = 100.0     # revocation evaluation starts
chop_dev_thresh   = 0.28      # |mid-0.5| commitment threshold
chop_lookback_sec = 60.0      # trailing window for "no recent 0.5-cross"
chop_confirm_sec  = 10.0      # anti-flip: revoke only after held this long
replace_shift     = 0.02      # up-only bid pull threshold (~2 ticks)
replace_dwell_sec = 4.0       # min interval between replaces of one side
freeze_sec        = 45.0      # clock trigger for CLOSING (completion-only)
```

## Testing

**Unit (pure):**
- `chop_revoke`: committed + no recent cross → True; committed but crossed within lookback → False;
  below dev_thresh → False; boundary of lookback window.
- `plan_requote`: pull-up ≥ shift → replace; down move → keep (no replace); within shift → keep;
  dwell not elapsed → keep; no resting → post.

**Regression checklist (dry-run of the new build — PLUMBING, not EV):**
1. Detector evaluates from t=100s (not before).
2. Revocation → CLOSING fires on a synthetic **late** trend; accumulation bids cancelled.
3. linked-pair cap still holds (no pairing fill ≥ $1) — incl. cap-override repricing a stale
   above-cap bid down (test: heavy_avg grows → light-side resting bid > cap → forced repost down).
4. `topbook_fillquality` writes `pair_cost_effective`, `rebate_accrued`, `detector`, `hindsight`
   (via the SHARED `window_regime` — same fn as the 51/28/20 baseline), `cap_replaces`, `shift_replaces`.
5. Bot **actually enters** a window (the thing the last dry-run did NOT show — confirm entry/quoting).
6. **Synthetic mid-trend that crosses 0.5 EARLY but commits AFTER 100s → revocation MUST fire** (the
   point-1 fix; without this the checklist misses the main bug).
7. No accumulation replace during freeze/CLOSING; replace respects dwell; a downward mid move does NOT
   replace (except cap-override); **completion FOK still fires in CLOSING** (CLOSING cancels only the
   resting accumulation bids, never the FOK completion of a leg we already hold).

Full suite stays green. Dry-run is a day or two max, purely to confirm the plumbing.

## Non-goals

- No new strategy; this refines `top_book`. `momentum`/`ladder`/`five_min` untouched.
- No live run in this scope (separate, gated, explicit "go"). No EV claim from dry-run.
- Entry gate (FRESH 230 + BALANCED 0.35-0.65) unchanged — it is already chop-aligned; the revocable
  detector sits on top.

## Deliverables

- `chop_revoke` + `plan_requote` (pure) + unit tests.
- CLOSING state machine + directional requote + causal/hindsight telemetry in `_top_book_window`.
- Config knobs in `Config` + run_control top_book.
- `scripts/_config_grid.py` offline threshold search.
- Regression dry-run (plumbing) after merge. No production behavior change unless `top_book` active.

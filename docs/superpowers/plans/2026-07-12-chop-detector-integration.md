# Chop-Detector Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Wire the chop-tactic into live `top_book`: a revocable CLOSING gate (sliding one-way chop-detector), a directional cancel/replace policy, causal+hindsight telemetry, and an offline grid-search for thresholds.

**Architecture:** Two pure helpers (`chop_revoke`, `plan_requote`) in `top_book_planner.py`; a CLOSING state machine + directional requote + telemetry in `_top_book_window` (merge_runner.py); config knobs; a grid-search script. TDD throughout. Live stays locked behind `LIVE_GO=1`; default dry-run.

**Spec:** `docs/superpowers/specs/2026-07-12-chop-detector-integration-design.md`

---

## Task 1: `chop_revoke` (pure sliding revocation) + tests

**Files:** Modify `quoter/runner/top_book_planner.py`; Test `tests/test_chop_gate.py`.

- [ ] **Step 1: Write failing tests.** Create `tests/test_chop_gate.py`:

```python
"""chop_revoke: sliding, causal trend-commit signal for the revocable CLOSING gate."""
from quoter.runner.top_book_planner import chop_revoke


def h(*pairs):
    return list(pairs)


def test_committed_and_quiet_tail_revokes():
    # mid 0.85 now, one-sided (>=0.5) for the whole 60s tail -> trend, revoke.
    hist = h((40, 0.55), (60, 0.7), (80, 0.8), (100, 0.85))
    assert chop_revoke(hist, now=100, dev_thresh=0.28, lookback_sec=60) is True


def test_crossed_in_tail_does_not_revoke():
    # committed at 0.85 now, but it dipped below 0.5 at t=80 (within the 60s tail) -> still chop.
    hist = h((40, 0.55), (60, 0.7), (80, 0.42), (100, 0.85))
    assert chop_revoke(hist, now=100, dev_thresh=0.28, lookback_sec=60) is False


def test_not_committed_does_not_revoke():
    # mid 0.60 -> |0.60-0.5| = 0.10 < 0.28 -> not committed.
    hist = h((60, 0.55), (80, 0.58), (100, 0.60))
    assert chop_revoke(hist, now=100, dev_thresh=0.28, lookback_sec=60) is False


def test_mid_trend_crosses_early_commits_late_revokes():
    # THE point-1 bug: oscillates around 0.5 up to ~90s (crosses several times), then commits up.
    # At now=160 the 60s tail [100,160] is one-sided high -> MUST revoke (a cumulative rule would not).
    hist = h((20, 0.46), (40, 0.54), (60, 0.48), (80, 0.53), (100, 0.62),
             (120, 0.78), (140, 0.86), (160, 0.9))
    assert chop_revoke(hist, now=160, dev_thresh=0.28, lookback_sec=60) is True
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_chop_gate.py -q` → FAIL (no `chop_revoke`).

- [ ] **Step 3: Implement.** Add to `quoter/runner/top_book_planner.py` (after `maker_rebate`):

```python
def chop_revoke(mid_hist, now, dev_thresh=0.28, lookback_sec=60.0):
    """Sliding, causal trend-commit signal for the revocable CLOSING gate. mid_hist: list of
    (rel_ts, up_mid) in time order. Returns True iff the window is committing to a trend at `now`:
      |mid(now) - 0.5| >= dev_thresh  AND  no 0.5-crossing in the trailing [now - lookback_sec, now].
    Cumulative history (crossed-ever) is deliberately NOT used: a mid-trend that oscillated early then
    commits late must revoke; a 'crossed ever' stamp would wrongly keep it chop forever."""
    if not mid_hist:
        return False
    cur = mid_hist[-1][1]
    if abs(cur - 0.5) < dev_thresh:
        return False
    tail = [(t, m) for (t, m) in mid_hist if t >= now - lookback_sec]
    for i in range(len(tail) - 1):
        if (tail[i][1] >= 0.5) != (tail[i + 1][1] >= 0.5):
            return False                       # crossed 0.5 in the tail -> still oscillating
    return True
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_chop_gate.py -q` → PASS (4).
- [ ] **Step 5: Commit** `feat(topbook): chop_revoke — sliding one-way trend-commit signal`.

## Task 2: `plan_requote` (directional cancel/replace) + tests

**Files:** Modify `quoter/runner/top_book_planner.py`; add to `tests/test_chop_gate.py`.

- [ ] **Step 1: Add failing tests** to `tests/test_chop_gate.py` (note: `plan_requote` returns a
3-tuple `(cancel, post, cap_sides)`):

```python
from quoter.runner.top_book_planner import plan_requote, TBQuote


def _q(side, price):
    return TBQuote(side, price, 5.0)


def test_pull_up_replaces_when_shift_and_dwell_met():
    resting = {"Up": (0.50, 5.0)}
    target = [_q("Up", 0.53)]                    # +0.03 >= 0.02 shift
    cancel, post, cap = plan_requote(resting, target, {"Up": 0.0}, now=10, replace_shift=0.02, dwell_sec=4)
    assert cancel == ["Up"] and len(post) == 1 and post[0].price == 0.53 and cap == []


def test_down_move_never_chases():
    resting = {"Up": (0.50, 5.0)}
    target = [_q("Up", 0.46)]                    # mid fell to our bid -> our plan, do NOT chase down
    cancel, post, cap = plan_requote(resting, target, {"Up": 0.0}, now=10)
    assert cancel == [] and post == [] and cap == []


def test_cap_override_forces_down_ignoring_shift_and_dwell():
    # INVARIANT (pair<$1): our resting bid 0.50 now sits ABOVE the linked-pair cap 0.44 (heavy_avg
    # grew). Must cancel+repost down to <=cap even though it's a down move AND dwell hasn't elapsed —
    # else a fill assembles a pair >= $1. Counted in cap_sides, not shift-driven.
    resting = {"Up": (0.50, 5.0)}
    cancel, post, cap = plan_requote(resting, [_q("Up", 0.44)], {"Up": 9.99}, now=10,
                                     caps={"Up": 0.44}, replace_shift=0.02, dwell_sec=4)
    assert cancel == ["Up"] and post and post[0].price == 0.44 and cap == ["Up"]


def test_within_shift_keeps():
    resting = {"Up": (0.50, 5.0)}
    cancel, post, cap = plan_requote(resting, [_q("Up", 0.505)], {"Up": 0.0}, now=10, replace_shift=0.02)
    assert cancel == [] and post == []


def test_dwell_not_elapsed_keeps():
    resting = {"Up": (0.50, 5.0)}
    cancel, post, cap = plan_requote(resting, [_q("Up", 0.55)], {"Up": 8.0}, now=10, dwell_sec=4)  # 2<4
    assert cancel == [] and post == []


def test_new_side_posts_and_dropped_side_cancels():
    cancel, post, cap = plan_requote({"Down": (0.10, 5.0)}, [_q("Up", 0.90)], {}, now=10)
    assert "Down" in cancel and any(q.side == "Up" for q in post)
```

- [ ] **Step 2: Run** → FAIL (no `plan_requote`).

- [ ] **Step 3: Implement.** Add to `top_book_planner.py`:

```python
def plan_requote(resting, target, last_replace, now, caps=None,
                 replace_shift=0.02, dwell_sec=4.0):
    """Directional, dwell-bounded cancel/replace for ACCUMULATION bids (replaces diff_quotes on the
    top_book accumulation path). A mid move DOWN to our bid is our PLAN (cheap fill on the dump) — do
    not chase down; a mid move UP away makes the bid dead — pulling up trades queue for fill-rate.
    resting: {side:(price,size)}; target: list[TBQuote] (already linked-pair capped); last_replace:
    {side: ts}; caps: {side: current linked-pair ceiling} (or None).
    Returns (cancel, post, cap_sides).
    Rules/side, in order:
      1. no resting -> post (new side).
      2. CAP-OVERRIDE (invariant, NOT queue-opt): if resting price > caps[side], cancel+repost down
         to the (capped) target, IGNORING direction and dwell — a resting bid above the current cap
         would assemble a pair >= $1 if filled (the cap tightens as heavy_avg grows, i.e. in trends).
         -> cap_sides.
      3. pull UP: target >= resting + shift AND dwell elapsed -> cancel+repost (shift-driven).
      4. else (down move / within shift / dwell not elapsed) -> keep.
    Sides absent from target -> cancel."""
    tgt = {q.side: q for q in target}
    cancel, post, cap_sides = [], [], []
    for q in target:
        if q.side not in resting:
            post.append(q)
            continue
        rp = resting[q.side][0]
        cap = caps.get(q.side) if caps else None
        if cap is not None and rp > cap + 1e-12:                 # 2. cap-override (invariant)
            cancel.append(q.side); post.append(q); cap_sides.append(q.side)
            continue
        if q.price >= rp + replace_shift and (now - last_replace.get(q.side, -1e18)) >= dwell_sec:
            cancel.append(q.side); post.append(q)                # 3. pull up (shift-driven)
    for s in resting:
        if s not in tgt:
            cancel.append(s)
    return cancel, post, cap_sides
```

- [ ] **Step 4: Run** → PASS. **Step 5: Full suite** green. **Step 6: Commit** `feat(topbook): plan_requote — directional up-only cancel/replace with dwell`.

## Task 3: Config knobs

**Files:** Modify `quoter/config.py`, `quoter/runner/run_control.py`.

- [ ] **Step 1:** In `quoter/config.py`, after `tb_link_margin`, add:

```python
    # ── chop-detector integration (revocable CLOSING gate + directional requote) ──
    chop_gate: bool = False           # enable the revocable chop-detector on top_book
    chop_detect_sec: float = 100.0    # revocation evaluation starts (needs path history)
    chop_dev_thresh: float = 0.28     # |mid-0.5| commitment threshold
    chop_lookback_sec: float = 60.0   # trailing window for "no recent 0.5-cross"
    chop_confirm_sec: float = 10.0    # anti-flip: revoke only after held this long
    replace_shift: float = 0.02       # up-only bid pull threshold
    replace_dwell_sec: float = 4.0    # min interval between replaces of one side
    freeze_sec: float = 45.0          # clock trigger for CLOSING (completion-only)
```

- [ ] **Step 2:** In `run_control.py` `top_book` CFG, add `chop_gate=(not _REGIME)` (on in neutral mode; keep default off) plus the knobs at their defaults. Keep `dry_run=not _LIVE_GO`.

- [ ] **Step 3:** Run `.venv/bin/python -c "from quoter.config import Config; Config(chop_gate=True)"` → no error. **Commit** `feat(config): chop-gate knobs (detector, requote, freeze)`.

## Task 4: CLOSING state machine + directional requote in `_top_book_window`

**Files:** Modify `quoter/runner/merge_runner.py`; Test `tests/test_topbook_chop_gate.py`.

Context: `_top_book_window` runs a per-tick loop (fetch books → plan_top_book → link_pair_bids →
diff_quotes → cancel/post → fill-credit → completion/sell → merge → topbook_quotes). We add: (a)
`mid_hist` tracking, (b) a one-way `closing` flag set by `chop_revoke` (held `chop_confirm_sec`) OR
`time_remaining <= freeze_sec`, (c) when `closing`: cancel accumulation bids + skip new accumulation
quoting (completion/merge/sell still run), (d) swap `diff_quotes` → `plan_requote` for accumulation.

- [ ] **Step 1: Write failing tests.** Create `tests/test_topbook_chop_gate.py` reusing the harness
from `tests/test_top_book_complete.py` (import its `_Ctl`, `_book`, `_make_runner`, `_run`, or copy
the minimal harness). Add `chop_gate=True` to the test Config. Tests:

```python
def test_closing_on_synthetic_late_trend(monkeypatch):
    # feed a rising Up book so chop_revoke fires after chop_detect_sec -> closing -> no new
    # accumulation posts after the flip; resting accumulation bids get cancelled.
    ...
    assert runner.state.last_event  # closing recorded; no accumulation post after revocation tick

def test_closing_by_clock_freeze(monkeypatch):
    # in the last freeze_sec, accumulation quoting stops (completion-only), even without a trend.
    ...

def test_no_revoke_on_choppy_book(monkeypatch):
    # book oscillating across 0.5 -> chop_revoke stays False -> keeps accumulating (posts continue).
    ...

def test_completion_still_fires_in_closing(monkeypatch):
    # after CLOSING (clock or trend), an existing naked leg with a <$1 completable pair STILL gets
    # a completion FOK — CLOSING cancels accumulation bids but never blocks completion of what we hold.
    ...  # assert a topbook_complete FOK is placed after the closing tick; naked -> 0
```
(Model the books via the harness `up`/`dn` params over successive ticks; assert on `ctl.places`
containing/omitting accumulation BUY posts after the decision tick, and on a `closing`-marker log.)

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement in `_top_book_window`:**
  - Near the window-local inits (with `last_mid = mid_at_entry`): add
    `mid_hist = []`, `closing = False`, `closing_reason = None`, `revoked_at = None`,
    `trend_since = None`, `last_replace = {"Up": -1e18, "Down": -1e18}`,
    `cap_replaces = 0`, `shift_replaces = 0`.
  - **SCOPE NOTE (fix):** `oids`/`resting` hold ONLY the resting ACCUMULATION maker bids. Completion
    and sell are TAKER FOK (immediate, never rest), so the CLOSING cancel below touches accumulation
    bids only — it does NOT cancel any pending completion. Completion/merge/sell run in CLOSING.
  - After `last_mid` is updated each tick, append: `mid_hist.append((300.0 - m.time_remaining(), last_mid))`.
  - Compute the CLOSING trigger each tick BEFORE quoting (only when `self.cfg.chop_gate`):
    ```python
    elapsed = 300.0 - m.time_remaining()
    if not closing:
        clock = m.time_remaining() <= self.cfg.freeze_sec
        trend = (self.cfg.chop_gate and elapsed >= self.cfg.chop_detect_sec
                 and chop_revoke(mid_hist, elapsed, self.cfg.chop_dev_thresh, self.cfg.chop_lookback_sec))
        trend_since = (trend_since if trend else None) or (elapsed if trend else None)
        trend_confirmed = trend and trend_since is not None and (elapsed - trend_since) >= self.cfg.chop_confirm_sec
        if clock or trend_confirmed:
            closing = True
            closing_reason = "trend" if trend_confirmed else "clock"
            revoked_at = round(elapsed, 0)
            await self._cancel_orders([o for oid in oids.values() for o in oid])   # drop accumulation
            for s in ("Up", "Down"):
                oids[s] = []; resting.pop(s, None)
            log.info("topbook_closing", slug=m.slug, reason=closing_reason, at_sec=revoked_at)
    ```
  - Gate accumulation quoting on `not closing`: wrap the `plan_top_book`→post block so it runs only
    `if not closing`. The completion/sell/merge blocks run regardless.
  - Replace the accumulation `diff_quotes(resting, target)` with `plan_requote`, passing the CURRENT
    linked-pair caps so the invariant holds (compute `caps[side] = round(1 - avg[other] - margin, 2)`
    for the light side, same formula link_pair_bids uses; `None` when no heavy leg):
    ```python
    caps = {s: (round(1.0 - avg[other] - self.cfg.tb_link_margin, 2)
                if (avg[other] is not None and inv[other] > inv[s]) else None)
            for s, other in (("Up", "Down"), ("Down", "Up"))}
    cancel, post, cap_sides = plan_requote(resting, target, last_replace, elapsed, caps,
                                           self.cfg.replace_shift, self.cfg.replace_dwell_sec)
    cap_replaces += len(cap_sides)
    shift_replaces += sum(1 for q in post if q.side in resting and q.side not in cap_sides)
    ```
  - **Fix (dwell at window start):** set `last_replace[side] = elapsed` on ANY post of that side
    (initial post AND repost), not only repost — else the first repost can churn with no dwell.
  - Guard: when `chop_gate` is False, behaviour is byte-identical to today (keep the old
    `diff_quotes` path under `else`; no mid_hist/closing/requote overhead).

- [ ] **Step 4: Run** the new tests → PASS. **Step 5: Full suite** green. **Step 6: Commit**
`feat(topbook): revocable CLOSING gate (chop_revoke or freeze) + directional requote`.

## Task 5: Causal + hindsight telemetry

**Files:** Modify `quoter/runner/merge_runner.py` (fillquality event + a hindsight classifier); Test `tests/test_topbook_chop_gate.py`.

- [ ] **Step 1: Extract the SHARED regime classifier** (fix: do NOT write a third). Move the
`regime(u_path)` logic (crosses of 0.5: `>=2 chop`, `==1 reversal`, `0 trend`) — the exact one used
in `scripts/_chop_detector_sim.py` that produced the 51/28/20 baseline — into a pure
`window_regime(mid_seq)` in `quoter/runner/top_book_planner.py`. Refactor `_chop_detector_sim.py`
(and `_chop_maker_sim.py`, `_window_behaviors.py` where applicable) to IMPORT it, so the live
hindsight label and the offline baseline are the SAME function (else the live confusion matrix is not
comparable to 51/28/20). Unit-test `window_regime` (chop/reversal/trend from synthetic paths).

- [ ] **Step 2: Add a failing test** asserting `topbook_fillquality` carries `detector`,
`revoked_at_sec`, `closing_reason`, `hindsight`, `cap_replaces`, `shift_replaces`.

- [ ] **Step 3: Implement.** In the `topbook_fillquality` `log.info`, add:
`detector="revoked" if closing_reason=="trend" else "chop"`, `revoked_at_sec=revoked_at`,
`closing_reason=closing_reason`, `hindsight=window_regime([m for _, m in mid_hist])`,
`cap_replaces=cap_replaces`, `shift_replaces=shift_replaces`. (Counting cap-driven vs shift-driven
replaces SEPARATELY is required — in a trend the cap-override reprices the light leg down repeatedly;
mixing it into shift-driven counts would spoil the live fill-rate interpretation.) pair_cost_effective
+ rebate_accrued already present.

- [ ] **Step 4: Run** → PASS. **Full suite** green. **Commit** `feat(topbook): causal+hindsight telemetry + cap/shift replace counters (shared window_regime)`.

## Task 6: Offline threshold grid-search script

**Files:** Create `scripts/_config_grid.py`.

- [ ] **Step 1:** Write `scripts/_config_grid.py` reusing `_chop_detector_sim` machinery. The 5m
market tick is **0.01** (verified from the market object: `minimum_tick_size = 0.01`), so the grid is
in real ticks with the default at the CENTER: `replace_shift ∈ {0.01, 0.02, 0.03}` (1/2/3 ticks) ×
`freeze_sec ∈ {30, 45, 60}` × `chop_lookback_sec ∈ {40, 60, 80}`. Run the maker-both sim (with
`chop_revoke`/`plan_requote` applied) over the tape files passed as argv; print a ranked table by mean
`pair_cost` and total PnL. Memory-safe (process one file at a time, as `_chop_detector_sim` does). No
production import beyond the pure helpers + exec_ab.

- [ ] **Step 2:** `.venv/bin/python -m py_compile scripts/_config_grid.py`. **Commit**
`feat(research): offline grid-search for chop-gate thresholds`.

## After all tasks

Final review, then the **regression dry-run** (plumbing, not EV) against the 7-point checklist in the
spec — confirm the detector fires at 100s, revocation on the synthetic mid-trend, freeze→completion-only,
directional requote + dwell, telemetry fields, and that the bot actually enters a window. Then the
grid-search picks the config, and the live measurement (separate, gated, explicit "go") runs the NEW
tactic.

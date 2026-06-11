# Trend Detector Time-Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the trend detector act only in the last `trend_gate_sec` (90s) of a window, so it stops firing on transient swings and sabotaging choppy windows.

**Architecture:** One config knob + a guard at the top of `detect_bias` (return NEUTRAL when `time_left > trend_gate_sec`) + pass `time_left` from the runner.

**Tech Stack:** Python 3.13, pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-trend-detector-time-gate-design.md`

---

### Task 1: Config knob + `detect_bias` time gate

**Files:**
- Modify: `quoter/config.py` (add `trend_gate_sec`)
- Modify: `quoter/runner/trend_detector.py` (`detect_bias` gains `time_left`, adds gate)
- Modify: `tests/test_trend_detector.py` (update 4 existing detect_bias calls + 2 new gate tests)

- [ ] **Step 1: Update tests (existing calls + new gate tests)**

In `tests/test_trend_detector.py`, the `cfg(**kw)` helper currently sets the trend knobs.
Add `trend_gate_sec=90.0` to its `base` dict so the helper stays valid:

```python
    base = dict(trend_enabled=True, trend_confidence=0.35, trend_buffer_sec=60.0,
                trend_vol_fallback=30.0, trend_stale_sec=10.0, trend_gate_sec=90.0)
```

Then REPLACE the four `detect_bias(...)` test functions with these (each now passes a
`time_left` INSIDE the gate so the probability rule still applies), and ADD two gate tests:

```python
def test_bias_up_when_clearly_above():
    assert detect_bias(100050.0, 100000.0, 20.0, 30.0, cfg()) == "UP"


def test_bias_down_when_clearly_below():
    assert detect_bias(99950.0, 100000.0, 20.0, 30.0, cfg()) == "DOWN"


def test_bias_neutral_near_strike():
    assert detect_bias(100005.0, 100000.0, 50.0, 30.0, cfg()) == "NEUTRAL"


def test_bias_time_decay():
    assert detect_bias(100008.0, 100000.0, 31.0, 30.0, cfg()) == "NEUTRAL"
    assert detect_bias(100008.0, 100000.0, 10.0, 30.0, cfg()) == "UP"


def test_gate_blocks_early_trend():
    # clear trend (z=10), but 200s left > gate 90 → NEUTRAL (ignore early swing)
    assert detect_bias(100200.0, 100000.0, 20.0, 200.0, cfg()) == "NEUTRAL"


def test_gate_allows_late_trend():
    # same clear trend, 30s left <= gate 90 → fires
    assert detect_bias(100200.0, 100000.0, 20.0, 30.0, cfg()) == "UP"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_trend_detector.py -v`
Expected: FAIL — `detect_bias()` takes 4 positional args but 5 given (signature not yet changed).

- [ ] **Step 3: Add the config knob**

In `quoter/config.py`, immediately after the line `trend_stale_sec: float = 10.0     # buffer newest entry older than this → NEUTRAL (fail-safe)`, add:

```python
    trend_gate_sec: float = 90.0      # detector acts only in the last N sec of the window
```

- [ ] **Step 4: Add `time_left` + gate to `detect_bias`**

In `quoter/runner/trend_detector.py`, replace the `detect_bias` function with:

```python
def detect_bias(price_now: float, strike: float, sigma_remaining: float,
                time_left: float, cfg: Config) -> str:
    """Return "UP" | "DOWN" | "NEUTRAL". Acts only in the last cfg.trend_gate_sec of the
    window (early swings are ignored as noise that will likely revert). Within the gate,
    suppress the side whose win-prob < trend_confidence. "UP" = Up winning → suppress Down
    (NO); "DOWN" = Down winning → suppress Up (YES)."""
    if time_left > cfg.trend_gate_sec:
        return "NEUTRAL"
    p_up = win_prob_up(price_now, strike, sigma_remaining)
    t = cfg.trend_confidence
    if p_up < t:
        return "DOWN"
    if p_up > 1.0 - t:
        return "UP"
    return "NEUTRAL"
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_trend_detector.py -v`
Expected: 11 passed (9 prior shape, with the 4 detect_bias ones updated + 2 new gate tests).

- [ ] **Step 6: Full suite + commit**

Run: `.venv/bin/python -m pytest -q` — NOTE: `tests/test_trend_config.py` does NOT assert
`trend_gate_sec`, so it still passes; expect all green. Then:
```bash
git add quoter/config.py quoter/runner/trend_detector.py tests/test_trend_detector.py
git commit -m "fix(trend): time-gate — detect_bias acts only in last trend_gate_sec (no choppy sabotage)"
```

---

### Task 2: Pass `time_left` from the runner

**Files:** Modify `quoter/runner/merge_runner.py` (`_trend_bias`).

- [ ] **Step 1: Update the `detect_bias` call**

In `quoter/runner/merge_runner.py`, find the `_trend_bias` method. Its last line is:
```python
        sig = sigma_remaining(self._btc_buf, time_left, self.cfg)
        return detect_bias(price_now, strike, sig, self.cfg)
```
Replace the `return` line with (insert `time_left` before `self.cfg`):
```python
        return detect_bias(price_now, strike, sig, time_left, self.cfg)
```

- [ ] **Step 2: Verify import + full suite**

Run: `.venv/bin/python -c "import quoter.runner.merge_runner" && .venv/bin/python -m pytest -q`
Expected: imports clean; full suite all green.

- [ ] **Step 3: Commit**

```bash
git add quoter/runner/merge_runner.py
git commit -m "fix(trend): pass time_left to detect_bias (wire the time-gate)"
```

---

## Self-review

**Spec coverage:**
- `trend_gate_sec=90.0` config knob → Task 1 Step 3 ✓
- `detect_bias` gains `time_left`, returns NEUTRAL when `time_left > trend_gate_sec` → Task 1 Step 4 ✓
- Runner passes `time_left` → Task 2 ✓
- Tests: gate blocks early trend, allows late trend, existing prob-rule tests still fire (within gate) → Task 1 Step 1 ✓

**Placeholder scan:** none — full code + commands in every step.

**Type consistency:** new signature `detect_bias(price_now, strike, sigma_remaining, time_left, cfg) -> str` is used identically in the tests (Task 1) and the runner call (Task 2). `time_left` is the same value already computed for `sigma_remaining` in `_trend_bias`. `cfg.trend_gate_sec` matches the config field added in Task 1.

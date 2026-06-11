# Binance Trend Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect trending windows from live Binance BTC price and suppress the ladder's losing side, so the bot sits out trends instead of buying the crashing loser.

**Architecture:** A pure `trend_detector` (win-prob from price-vs-strike and time-left, one knob) feeds a `trend_bias` into `plan_ladder`, which suppresses the loser's rungs (reusing the rung-pull path). The runner runs the existing `BinanceWS` as a background task and keeps a rolling price buffer.

**Tech Stack:** Python 3.13, stdlib `math` (erf), pytest. Reuses `quoter/feeds/binance_ws.py` (`BinanceWS`), `quoter/runner/ladder_planner.py` (`plan_ladder`).

**Spec:** `docs/superpowers/specs/2026-06-11-trend-detector-design.md`

---

## File structure

- `quoter/config.py` — add 5 trend knobs.
- `quoter/runner/trend_detector.py` — NEW pure: `win_prob_up`, `sigma_remaining`, `detect_bias`.
- `tests/test_trend_detector.py` — NEW unit tests.
- `quoter/runner/ladder_planner.py` — add `trend_bias` param to `plan_ladder` (suppress loser).
- `tests/test_ladder_planner.py` — add a trend-bias test.
- `quoter/runner/merge_runner.py` — run `BinanceWS` + price buffer; capture strike + compute bias in `_ladder_window`.

---

### Task 1: Config knobs

**Files:** Modify `quoter/config.py`; Create `tests/test_trend_config.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trend_config.py`:

```python
from quoter.config import Config


def test_config_has_trend_knobs_with_defaults():
    c = Config()
    assert c.trend_enabled is True
    assert abs(c.trend_confidence - 0.35) < 1e-9
    assert abs(c.trend_buffer_sec - 60.0) < 1e-9
    assert abs(c.trend_vol_fallback - 30.0) < 1e-9
    assert abs(c.trend_stale_sec - 10.0) < 1e-9


def test_config_trend_knobs_overridable():
    c = Config(trend_enabled=False, trend_confidence=0.30)
    assert c.trend_enabled is False and abs(c.trend_confidence - 0.30) < 1e-9
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trend_config.py -v`
Expected: FAIL `TypeError: __init__() got an unexpected keyword argument 'trend_enabled'`

- [ ] **Step 3: Add the fields**

In `quoter/config.py`, immediately after the ladder knobs block (the line `per_window_cap: float = 12.0  # $ ceiling on committed spend per window`), add:

```python

    # phase-23 Binance trend detector
    trend_enabled: bool = True
    trend_confidence: float = 0.35    # THE knob: suppress a side when its win-prob < this
    trend_buffer_sec: float = 60.0    # rolling price-buffer window (seconds)
    trend_vol_fallback: float = 30.0  # fallback $-vol of BTC over a 5m window if buffer thin
    trend_stale_sec: float = 10.0     # buffer newest entry older than this → NEUTRAL (fail-safe)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trend_config.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_trend_config.py
git commit -m "feat(config): Binance trend-detector knobs (enabled/confidence/buffer/vol/stale)"
```

---

### Task 2: Pure `trend_detector`

**Files:** Create `quoter/runner/trend_detector.py`; Create `tests/test_trend_detector.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend_detector.py`:

```python
"""Pure trend detector: win-prob, vol estimate, bias decision."""

from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining, win_prob_up


def cfg(**kw):
    base = dict(trend_enabled=True, trend_confidence=0.35, trend_buffer_sec=60.0,
                trend_vol_fallback=30.0, trend_stale_sec=10.0)
    base.update(kw)
    return Config(**base)


def test_win_prob_at_strike_is_half():
    assert abs(win_prob_up(100000.0, 100000.0, 20.0) - 0.5) < 1e-9


def test_win_prob_far_above_and_below():
    assert win_prob_up(100100.0, 100000.0, 20.0) > 0.99   # +5 sigma
    assert win_prob_up(99900.0, 100000.0, 20.0) < 0.01     # -5 sigma


def test_bias_up_when_clearly_above():
    # +2.5 sigma → p_up ~0.994 > 0.65 → Down is loser → suppress Down → "UP"
    assert detect_bias(100050.0, 100000.0, 20.0, cfg()) == "UP"


def test_bias_down_when_clearly_below():
    assert detect_bias(99950.0, 100000.0, 20.0, cfg()) == "DOWN"


def test_bias_neutral_near_strike():
    # tiny gap vs big sigma → p_up ~0.54 → between 0.35 and 0.65 → NEUTRAL
    assert detect_bias(100005.0, 100000.0, 50.0, cfg()) == "NEUTRAL"


def test_bias_time_decay():
    # same $8 gap: large sigma (early) → NEUTRAL; small sigma (late) → fires
    assert detect_bias(100008.0, 100000.0, 31.0, cfg()) == "NEUTRAL"
    assert detect_bias(100008.0, 100000.0, 10.0, cfg()) == "UP"


def test_sigma_scales_with_time_left():
    # same buffer, more time left → bigger expected move (×sqrt(time_left))
    buf = [(100000.0 + (i % 2) * 4.0, i * 0.5) for i in range(20)]  # small jitter, no drift
    s_late = sigma_remaining(buf, 25.0, cfg())
    s_early = sigma_remaining(buf, 100.0, cfg())
    assert s_early > s_late * 1.8   # ~2x for 4x the time


def test_sigma_ignores_steady_drift():
    # a pure steady uptrend (no noise) → vol estimate ~floor (drift removed), so the
    # detector still sees the gap as significant rather than as "high volatility".
    buf = [(100000.0 + i * 3.0, i * 0.5) for i in range(20)]  # +$3 every 0.5s, no noise
    sig = sigma_remaining(buf, 30.0, cfg())
    assert sig < 20.0   # drift removed → low noise vol (not inflated by the trend)


def test_sigma_thin_buffer_uses_fallback():
    sig = sigma_remaining([(100000.0, 0.0)], 300.0, cfg())   # too few points
    assert abs(sig - 30.0) < 1e-6   # trend_vol_fallback * sqrt(300/300)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trend_detector.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'quoter.runner.trend_detector'`

- [ ] **Step 3: Implement**

Create `quoter/runner/trend_detector.py`:

```python
"""Pure Binance trend detector — decides which side of a 5m up/down market is winning.

No I/O. From the BTC price now vs the window's strike (open price) and the time left,
estimate P(Up wins) under a normal model and return a bias telling plan_ladder to
suppress the losing side's rungs (sit out the trend). One knob: cfg.trend_confidence.
The volatility used is NOISE vol (drift removed), so a steady trend is seen as a
significant move, not as "high volatility".
"""

from __future__ import annotations

import math

from quoter.config import Config


def win_prob_up(price_now: float, strike: float, sigma_remaining: float) -> float:
    """P(BTC_close >= strike) ~ Phi((price_now - strike) / sigma_remaining)."""
    if sigma_remaining <= 0:
        return 1.0 if price_now >= strike else 0.0
    z = (price_now - strike) / sigma_remaining
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def sigma_remaining(prices: list[tuple[float, float]], time_left: float, cfg: Config) -> float:
    """Expected $-stdev of the BTC move over ``time_left`` seconds, from a recent
    ``(price, ts)`` buffer. Uses drift-removed tick-to-tick noise as the per-sqrt-second
    vol; falls back to ``cfg.trend_vol_fallback`` (defined over a 5m=300s window) when the
    buffer is too thin. Floored at $1 to avoid blow-up as ``time_left -> 0``."""
    tl = max(time_left, 0.0)
    if len(prices) >= 6:
        vals = [p for p, _ in prices]
        diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
        mean_d = sum(diffs) / len(diffs)
        resid = [d - mean_d for d in diffs]                    # remove drift → noise only
        rms = (sum(r * r for r in resid) / len(resid)) ** 0.5
        span = max(prices[-1][1] - prices[0][1], 1.0)
        avg_dt = max(span / len(diffs), 0.001)
        vol_per_sqrt_sec = rms / avg_dt ** 0.5
        sig = vol_per_sqrt_sec * tl ** 0.5
    else:
        sig = cfg.trend_vol_fallback * (tl / 300.0) ** 0.5
    return max(sig, 1.0)


def detect_bias(price_now: float, strike: float, sigma_remaining: float, cfg: Config) -> str:
    """Return "UP" | "DOWN" | "NEUTRAL". Suppress the side whose win-prob < trend_confidence.
    "UP" = Up is winning → suppress Down (NO). "DOWN" = Down winning → suppress Up (YES)."""
    p_up = win_prob_up(price_now, strike, sigma_remaining)
    t = cfg.trend_confidence
    if p_up < t:
        return "DOWN"          # Up is the near-certain loser → suppress YES
    if p_up > 1.0 - t:
        return "UP"            # Down is the near-certain loser → suppress NO
    return "NEUTRAL"
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trend_detector.py -v`
Expected: 9 passed. If `test_sigma_ignores_steady_drift` fails, the drift-removal is wrong — debug it, do not weaken the test.

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/trend_detector.py tests/test_trend_detector.py
git commit -m "feat(trend): pure detector — win-prob, drift-removed vol, bias (one-knob)"
```

---

### Task 3: `plan_ladder` accepts `trend_bias`

**Files:** Modify `quoter/runner/ladder_planner.py`; Modify `tests/test_ladder_planner.py`.

- [ ] **Step 1: Add the failing test**

Append to `tests/test_ladder_planner.py`:

```python
def test_trend_bias_suppresses_losing_side():
    # bias "UP" → Down is loser → no NO rungs posted; YES rungs still posted
    p_up = plan_ladder(
        yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
        inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
        resting={"YES": [], "NO": []}, cfg=cfg(), trend_bias="UP")
    assert any(q.side == "YES" for q in p_up.posts)
    assert not any(q.side == "NO" for q in p_up.posts)
    # bias "DOWN" → Up is loser → no YES rungs
    p_dn = plan_ladder(
        yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
        inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
        resting={"YES": [], "NO": []}, cfg=cfg(), trend_bias="DOWN")
    assert not any(q.side == "YES" for q in p_dn.posts)
    assert any(q.side == "NO" for q in p_dn.posts)


def test_trend_bias_neutral_is_default():
    # default NEUTRAL → both sides (same as no bias passed)
    p = plan_ladder(
        yes_bid=0.44, no_bid=0.54, yes_ask=0.99, no_ask=0.99, entry_mid=0.45,
        inv_yes=0, inv_no=0, yes_cost=0.0, no_cost=0.0, committed=0.0,
        resting={"YES": [], "NO": []}, cfg=cfg())
    assert any(q.side == "YES" for q in p.posts) and any(q.side == "NO" for q in p.posts)
```

- [ ] **Step 2: Run to verify the new test fails**

Run: `.venv/bin/python -m pytest tests/test_ladder_planner.py -k trend_bias -v`
Expected: FAIL `TypeError: plan_ladder() got an unexpected keyword argument 'trend_bias'`

- [ ] **Step 3: Add the param + suppression**

In `quoter/runner/ladder_planner.py`, change the `plan_ladder` signature to add the new keyword AFTER `cfg` (so existing positional/keyword callers are unaffected):

```python
    resting: dict[str, list[RestingOrder]],
    cfg: Config,
    trend_bias: str = "NEUTRAL",
) -> LadderPlan:
```

Then, immediately AFTER the block that builds `desired` (the two `if inv_... < target ...` lines that set `desired["YES"]` / `desired["NO"]`) and BEFORE the `for side in ("YES", "NO"):` diff loop, insert:

```python
    # Trend detector: suppress the losing side's rungs (sit out the trend).
    if trend_bias == "UP":
        desired["NO"] = []     # Up winning → Down is the loser
    elif trend_bias == "DOWN":
        desired["YES"] = []    # Down winning → Up is the loser
```

- [ ] **Step 4: Run to verify all pass**

Run: `.venv/bin/python -m pytest tests/test_ladder_planner.py -v`
Expected: all pass (7 original + 2 new = 9).

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/ladder_planner.py tests/test_ladder_planner.py
git commit -m "feat(ladder): plan_ladder honors trend_bias — suppress losing side's rungs"
```

---

### Task 4: Live wiring in `merge_runner`

**Files:** Modify `quoter/runner/merge_runner.py`.

Live I/O (no unit test; validated operator-gated later). Runs `BinanceWS` as a background
task, keeps a rolling buffer, captures the strike at window entry, computes the bias each
tick, and passes it to `plan_ladder`.

- [ ] **Step 1: Add imports**

In `quoter/runner/merge_runner.py`, after `from quoter.runner.ladder_planner import plan_ladder`, add:

```python
from quoter.runner.trend_detector import detect_bias, sigma_remaining
from quoter.feeds.binance_ws import BinanceWS
```

- [ ] **Step 2: Init the Binance feed + buffer in `__init__`**

In `MergeRunner.__init__`, after the line `self._shutdown = False`, add:

```python
        self._btc_buf: list[tuple[float, float]] = []   # (price, monotonic_ts)
        self._binance = BinanceWS(("BTC",), self._on_btc) if cfg.trend_enabled else None
        self._binance_task = None
```

- [ ] **Step 3: Add the price callback + bias helper methods**

Add these two methods to the `MergeRunner` class (e.g. right after `__init__`):

```python
    async def _on_btc(self, asset: str, price: float, ts: float) -> None:
        """BinanceWS callback: append to the rolling buffer (arrival-time stamped) and
        drop entries older than trend_buffer_sec."""
        now = monotonic()
        self._btc_buf.append((price, now))
        cutoff = now - self.cfg.trend_buffer_sec
        self._btc_buf = [(p, t) for (p, t) in self._btc_buf if t >= cutoff]

    def _trend_bias(self, strike: float | None, time_left: float) -> str:
        """Current trend bias, or NEUTRAL when disabled / no strike / buffer stale."""
        if not self.cfg.trend_enabled or strike is None or not self._btc_buf:
            return "NEUTRAL"
        price_now, last_ts = self._btc_buf[-1]
        if monotonic() - last_ts > self.cfg.trend_stale_sec:
            return "NEUTRAL"
        sig = sigma_remaining(self._btc_buf, time_left, self.cfg)
        return detect_bias(price_now, strike, sig, self.cfg)
```

- [ ] **Step 4: Start the Binance task in `run_forever`**

In `run_forever`, immediately after the line `log.info("runner_started", mode=self.state.mode)`, add:

```python
        if self._binance is not None and self._binance_task is None:
            self._binance_task = asyncio.create_task(self._binance.run())
```

- [ ] **Step 5: Capture strike + pass bias in `_ladder_window`**

In `_ladder_window`, after the line `local = LocalInventory()` (the init block), add:

```python
        strike = self._btc_buf[-1][0] if self._btc_buf else None
```

Then, in the same method, change the `plan_ladder(...)` call to pass the bias. Find:

```python
                plan = plan_ladder(
                    yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
                    entry_mid=entry_mid, inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=local.cost["YES"], no_cost=local.cost["NO"],
                    committed=committed, resting=resting, cfg=self.cfg)
```

and replace with (add `trend_bias`, and a state line so the dashboard shows it):

```python
                tbias = self._trend_bias(strike, m.time_remaining())
                self.state.last_event = f"laddering {m.slug} (bias {tbias})"
                plan = plan_ladder(
                    yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
                    entry_mid=entry_mid, inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=local.cost["YES"], no_cost=local.cost["NO"],
                    committed=committed, resting=resting, cfg=self.cfg,
                    trend_bias=tbias)
```

- [ ] **Step 6: Verify import + full suite**

Run: `.venv/bin/python -c "import quoter.runner.merge_runner" && .venv/bin/python -m pytest -q`
Expected: imports clean; full suite all green (no regressions). If import fails, check that
`BinanceWS`, `monotonic`, `detect_bias`, `sigma_remaining` are all imported and that
`_on_btc`/`_trend_bias` are correctly indented as methods.

- [ ] **Step 7: Commit**

```bash
git add quoter/runner/merge_runner.py
git commit -m "feat(trend): wire BinanceWS + price buffer + bias into _ladder_window"
```

---

## Self-review

**Spec coverage:**
- Config knobs (enabled/confidence/buffer/vol/stale) → Task 1 ✓
- Pure model: `win_prob_up` (Φ via erf), `sigma_remaining` (drift-removed noise vol + fallback + floor), `detect_bias` (one-knob, UP/DOWN/NEUTRAL) → Task 2 + tests ✓
- `plan_ladder` suppresses loser via `trend_bias`, default NEUTRAL keeps old callers → Task 3 + tests ✓
- Live wiring: BinanceWS background task, rolling buffer, strike-at-entry, bias each tick → Task 4 ✓
- Fail-safe NEUTRAL (disabled / no strike / stale buffer) → Task 4 `_trend_bias` ✓
- Time-decay (√time_left) + vol auto-widening + reversal-reopens (recomputed each tick) → Task 2 model + Task 4 per-tick call ✓
- Detector only suppresses NEW rungs; never sells (held inventory untouched by plan_ladder) ✓

**Placeholder scan:** none — every step has full code + exact commands.

**Type consistency:** `detect_bias(price_now, strike, sigma_remaining, cfg) -> str`,
`sigma_remaining(prices: list[tuple[float,float]], time_left, cfg) -> float`,
`win_prob_up(price_now, strike, sigma_remaining) -> float`, and `plan_ladder(..., trend_bias="NEUTRAL")`
are used identically across Tasks 2/3/4. Bias strings "UP"/"DOWN"/"NEUTRAL" match between
`detect_bias` (Task 2) and the `plan_ladder` suppression (Task 3). `BinanceWS(("BTC",), self._on_btc)`
matches the existing constructor `BinanceWS(assets, on_price)`; callback signature
`_on_btc(self, asset, price, ts)` matches `PriceCallback = (asset, price, ts)`. Buffer entries are
`(price, monotonic_ts)`; `sigma_remaining` reads `prices[-1][1] - prices[0][1]` consistently.

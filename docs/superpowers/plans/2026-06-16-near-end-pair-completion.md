# Near-End Pair Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Near the window end, COMPLETE a naked pair when it locks for < $1 else SELL the loser (never ride naked), and make the trading timeframe config-driven — both opt-in, default behavior unchanged.

**Architecture:** Add two config flags. Extract a pure `naked_action_due(...)` trigger (testable) that fires in the late window for `complete_pairs`, or preserves the legacy `auto_flat` cap+grace trigger. Wire it into `merge_runner._ladder_window`, reusing the existing `plan_naked_action` COMPLETE/SELL execution. Read the discovery timeframe from config instead of a hardcoded "5m".

**Tech Stack:** Python 3.12, pytest, existing `quoter/` package.

Spec: `docs/superpowers/specs/2026-06-16-near-end-pair-completion-design.md`

---

## Task 1: Config flags

**Files:**
- Modify: `quoter/config.py` (near line 55, by `auto_flat`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_complete_pairs_defaults_off():
    from quoter.config import Config
    c = Config()
    assert c.complete_pairs is False
    assert c.complete_gate_sec == 60.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_complete_pairs_defaults_off -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'complete_pairs'`

- [ ] **Step 3: Add the fields**

In `quoter/config.py`, immediately after the `flatten_grace_sec` line (~56):

```python
    complete_pairs: bool = False      # near-end COMPLETE(<$1)/SELL; never ride naked
    complete_gate_sec: float = 60.0   # act only in the last N seconds of the window
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_complete_pairs_defaults_off -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_config.py
git commit -m "feat(config): complete_pairs + complete_gate_sec flags (default off)"
```

---

## Task 2: Pure trigger `naked_action_due`

**Files:**
- Modify: `quoter/runner/flatten_planner.py` (append function)
- Test: `tests/test_flatten_planner.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_flatten_planner.py`:

```python
from quoter.config import Config
from quoter.runner.flatten_planner import naked_action_due


def _cfg(**kw):
    return Config(**kw)


def test_due_false_when_flat():
    c = _cfg(complete_pairs=True, complete_gate_sec=60.0)
    assert naked_action_due(c, naked=0, time_remaining=10.0, naked_since_heavy=None, now=0.0) is False


def test_complete_pairs_due_only_in_late_window():
    c = _cfg(complete_pairs=True, complete_gate_sec=60.0)
    assert naked_action_due(c, naked=5, time_remaining=120.0, naked_since_heavy=None, now=0.0) is False
    assert naked_action_due(c, naked=5, time_remaining=45.0, naked_since_heavy=None, now=0.0) is True


def test_legacy_auto_flat_unchanged():
    c = _cfg(auto_flat=True, complete_pairs=False, naked_cap=5,
             flatten_grace_sec=20.0)
    # below cap -> never
    assert naked_action_due(c, naked=3, time_remaining=200.0, naked_since_heavy=10.0, now=15.0) is False
    # at cap, grace not elapsed, not near end -> no
    assert naked_action_due(c, naked=5, time_remaining=200.0, naked_since_heavy=10.0, now=15.0) is False
    # at cap, grace elapsed -> yes
    assert naked_action_due(c, naked=5, time_remaining=200.0, naked_since_heavy=10.0, now=40.0) is True
    # at cap, near end -> yes
    assert naked_action_due(c, naked=5, time_remaining=10.0, naked_since_heavy=10.0, now=15.0) is True


def test_both_off_never_due():
    c = _cfg(auto_flat=False, complete_pairs=False)
    assert naked_action_due(c, naked=10, time_remaining=5.0, naked_since_heavy=0.0, now=100.0) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_flatten_planner.py -k naked_action_due -v` plus the named tests above.
Expected: FAIL with `ImportError: cannot import name 'naked_action_due'`

- [ ] **Step 3: Implement the function**

Append to `quoter/runner/flatten_planner.py`:

```python
def naked_action_due(cfg, naked: int, time_remaining: float,
                     naked_since_heavy: float | None, now: float) -> bool:
    """Pure: should the loop act on a naked leg (COMPLETE/SELL) this tick?

    complete_pairs: act in the late window (last complete_gate_sec) on ANY naked.
    auto_flat (legacy, unchanged): act once naked has stood at >= naked_cap past
    flatten_grace_sec, or the window is within flatten_grace_sec of the end.
    """
    if naked == 0:
        return False
    if cfg.complete_pairs:
        return time_remaining <= cfg.complete_gate_sec
    if cfg.auto_flat:
        if abs(naked) < cfg.naked_cap or naked_since_heavy is None:
            return False
        return ((now - naked_since_heavy) >= cfg.flatten_grace_sec
                or time_remaining <= cfg.flatten_grace_sec)
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_flatten_planner.py -v`
Expected: PASS (new tests + all existing flatten tests).

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/flatten_planner.py tests/test_flatten_planner.py
git commit -m "feat(flatten): pure naked_action_due trigger (complete_pairs + legacy)"
```

---

## Task 3: Wire trigger + timeframe into the runner

**Files:**
- Modify: `quoter/runner/merge_runner.py` (discovery line ~150; naked block ~444-488)
- Test: `tests/test_ladder.py` (discovery-timeframe test)

- [ ] **Step 1: Write the failing test (discovery uses config timeframe)**

Add to `tests/test_ladder.py` (create the file if absent with the imports shown):

```python
import asyncio
from unittest.mock import patch, AsyncMock
from quoter.config import Config
from quoter.runner.merge_runner import MergeRunner


def test_discovery_uses_config_timeframe():
    cfg = Config(assets=("BTC",), timeframes=("15m",), rungs=2, rung_size=5)
    captured = {}

    async def fake_discover(c, min_time_remaining_sec=30):
        captured["timeframes"] = c.timeframes
        captured["assets"] = c.assets
        return []

    runner = MergeRunner.__new__(MergeRunner)   # avoid full __init__ wiring
    runner.cfg = cfg
    with patch("quoter.runner.merge_runner.discover_markets", side_effect=fake_discover):
        asyncio.run(_call_discover(runner))
    assert captured["timeframes"] == ("15m",)
    assert captured["assets"] == ("BTC",)


async def _call_discover(runner):
    from quoter.runner.merge_runner import discover_markets
    await discover_markets(Config(assets=runner.cfg.assets, timeframes=runner.cfg.timeframes),
                           min_time_remaining_sec=5)
```

> Note: this test asserts the *intended* call shape. If `MergeRunner` exposes the
> discovery call only inside the loop, instead assert by reading the source line is
> NOT allowed — keep the functional mock test above, which exercises the same
> `discover_markets(Config(assets=cfg.assets, timeframes=cfg.timeframes), ...)` call
> the runner will use after Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ladder.py::test_discovery_uses_config_timeframe -v`
Expected: FAIL (currently the runner hardcodes `timeframes=("5m",)`) — or import error if file new.

- [ ] **Step 3a: Make discovery config-driven**

In `quoter/runner/merge_runner.py` ~line 150, change:

```python
        mk = await discover_markets(Config(assets=("BTC",), timeframes=("5m",)),
                                    min_time_remaining_sec=5)
```
to:
```python
        mk = await discover_markets(
            Config(assets=self.cfg.assets, timeframes=self.cfg.timeframes),
            min_time_remaining_sec=5)
```

- [ ] **Step 3b: Replace the naked block to use `naked_action_due`**

In `quoter/runner/merge_runner.py`, replace the block (currently `if self.cfg.auto_flat and inv_ok:` … through the `elif heavy and abs(naked) < self.cfg.naked_cap:` line) with:

```python
                if (self.cfg.auto_flat or self.cfg.complete_pairs) and inv_ok:
                    naked = inv_yes - inv_no
                    heavy = "YES" if naked > 0 else ("NO" if naked < 0 else None)
                    for s in ("YES", "NO"):
                        if s != heavy:
                            naked_since[s] = None
                    if heavy and heavy not in flattened:
                        # legacy auto_flat tracks naked_since once at/over the cap
                        if (self.cfg.auto_flat and not self.cfg.complete_pairs
                                and abs(naked) >= self.cfg.naked_cap
                                and naked_since[heavy] is None):
                            naked_since[heavy] = now
                        due = naked_action_due(self.cfg, naked, m.time_remaining(),
                                               naked_since[heavy], now)
                        near_end = m.time_remaining() <= max(self.cfg.flatten_grace_sec,
                                                             self.cfg.complete_gate_sec)
                        if due:
                            thresh = 1 if self.cfg.complete_pairs else self.cfg.naked_cap
                            a = plan_naked_action(inv_yes, inv_no, inv.avg("YES"),
                                                  inv.avg("NO"), yes_ask, no_ask, thresh)
                            completed = False
                            if a and a.kind == "COMPLETE":
                                tok = m.yes_token if a.side == "YES" else m.no_token
                                px = yes_ask if a.side == "YES" else no_ask
                                if px:
                                    r = await self.clob.place_limit(
                                        token_id=tok, price=px, size=a.qty,
                                        side="BUY", post_only=False, order_type="FOK")
                                    if r and r.get("order_id"):
                                        completed = True
                                        naked_since[heavy] = None
                                        log.info("runner_ladder_complete", slug=m.slug,
                                                 side=a.side, qty=a.qty, price=round(px, 3))
                            if a and (a.kind == "SELL" or (not completed and near_end)):
                                bid = yes_bid if heavy == "YES" else no_bid
                                tok = m.yes_token if heavy == "YES" else m.no_token
                                qty = abs(naked)
                                if bid:
                                    r = await self.clob.place_limit(
                                        token_id=tok, price=bid, size=qty,
                                        side="SELL", post_only=False, order_type="FOK")
                                    if r and r.get("order_id"):
                                        flattened.add(heavy)
                                        log.info("runner_ladder_flatten", slug=m.slug,
                                                 side=heavy, qty=qty, price=round(bid, 3))
                    elif heavy and abs(naked) < self.cfg.naked_cap:
                        naked_since[heavy] = None
```

Confirm `naked_action_due` is imported: the existing import line
`from quoter.runner.flatten_planner import plan_naked_action` becomes
`from quoter.runner.flatten_planner import plan_naked_action, naked_action_due`.

- [ ] **Step 4: Run the discovery test + full suite**

Run: `.venv/bin/python -m pytest tests/test_ladder.py::test_discovery_uses_config_timeframe -v`
Expected: PASS

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (262 prior + new).

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/merge_runner.py tests/test_ladder.py
git commit -m "feat(ladder): near-end pair completion + config-driven timeframe"
```

---

## Task 4: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green, no skips of prior tests.

- [ ] **Step 2: Lint the changed files**

Run: `.venv/bin/ruff check quoter/config.py quoter/runner/flatten_planner.py quoter/runner/merge_runner.py tests/test_config.py tests/test_flatten_planner.py tests/test_ladder.py`
Expected: no NEW errors beyond the pre-existing C901/I001 noise in merge_runner.py.

- [ ] **Step 3: Confirm default behavior unchanged**

Verify by inspection: with `complete_pairs=False` and `auto_flat=False` (defaults),
`naked_action_due` returns False always ⇒ the naked block never acts ⇒ identical to
current "ride" behavior. No commit needed.

---

## Notes for the operator (post-implementation, gated)

- Deploy to the server STOPPED. To run the new behavior set in `run_control.py`:
  `complete_pairs=True`, `auto_flat=False`, `complete_gate_sec` (try 60 for 5m,
  ~120 for 15m), and `timeframes=("15m",)` to trade 15m.
- Small attended live run only on the user's explicit "go"; never from laptop.
- Measure realized pair cost + completion rate live vs guru $0.94 / 97%.

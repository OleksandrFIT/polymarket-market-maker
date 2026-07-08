# Momentum-Take Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `momentum` bot strategy (0xb27b's decoded system: taker-chase the momentum side + cheap fader + merge floor + small capped directional residual, never sell), live-locked, without touching the passive `top_book` path.

**Architecture:** New pure `taker_fee` helper; new Config fields; new `momentum` CFG in run_control (live-locked); new `_momentum_window` tick loop in merge_runner dispatched from `run_forever`. Reuses `chase_signal`, taker FOK execution, `plan_merge`, `_merge_pairs`, `_order_matched`, `_best`, the watchdog.

**Tech Stack:** Python 3, pytest. Use `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-07-08-momentum-take-design.md`

**Server run notes:** live only via `STRATEGY=momentum LIVE_GO=1` on the AWS box (ask user for current IP); default STOPPED + dry-run. Edge is measurable ONLY live — offline/unit tests cover mechanics.

---

## File Structure

- Modify: `quoter/runner/top_book_planner.py` — add pure `taker_fee(price)`.
- Modify: `quoter/config.py` — add `mom_lookback`, `mom_threshold`, `mom_chase_max`, `mom_residual_cap`.
- Modify: `quoter/runner/run_control.py` — add `STRATEGY == "momentum"` CFG (live-locked).
- Modify: `quoter/runner/merge_runner.py` — import `chase_signal` + `taker_fee`; add `_momentum_window`; dispatch it in `run_forever`.
- Create: `tests/test_taker_fee.py`, `tests/test_momentum_window.py`, extend `tests/test_run_control_cfg.py`.

---

### Task 1: `taker_fee` pure helper (TDD)

**Files:** Modify `quoter/runner/top_book_planner.py`; create `tests/test_taker_fee.py`.

- [ ] **Step 1: Write failing tests** in `tests/test_taker_fee.py`:

```python
"""Crypto taker fee per share = 1.80% * min(price, 1-price): peaks at 0.50, ~0 at extremes."""
from quoter.runner.top_book_planner import taker_fee


def test_peak_at_half():
    assert abs(taker_fee(0.50) - 0.009) < 1e-9        # 0.018 * 0.5


def test_cheap_at_extremes():
    assert taker_fee(0.95) < taker_fee(0.50)
    assert abs(taker_fee(0.95) - 0.018 * 0.05) < 1e-9
    assert abs(taker_fee(0.05) - 0.018 * 0.05) < 1e-9


def test_symmetric():
    assert abs(taker_fee(0.30) - taker_fee(0.70)) < 1e-9


def test_bounds_clamped():
    assert taker_fee(0.0) == 0.0 and taker_fee(1.0) == 0.0
```

- [ ] **Step 2: Run — expect ImportError / fail:** `.venv/bin/python -m pytest tests/test_taker_fee.py -q`

- [ ] **Step 3: Implement** — append to `quoter/runner/top_book_planner.py`:

```python
def taker_fee(price: float, rate: float = 0.018) -> float:
    """Polymarket crypto TAKER fee per share: `rate * min(price, 1-price)` — peaks at 0.50
    (~0.9c at rate 1.8%) and falls to ~0 at the 0/1 extremes. 0xb27b trades at the extremes
    precisely to minimise this. Maker fills pay 0 and never call this."""
    p = min(max(price, 0.0), 1.0)
    return rate * min(p, 1.0 - p)
```

- [ ] **Step 4: Run — expect PASS.** `.venv/bin/python -m pytest tests/test_taker_fee.py -q`

- [ ] **Step 5: Commit** — `git add quoter/runner/top_book_planner.py tests/test_taker_fee.py && git commit -m "feat(momentum): taker_fee helper (peaks at 0.50, ~0 at extremes)"`

---

### Task 2: Config fields + run_control `momentum` CFG (TDD on the cfg guard)

**Files:** Modify `quoter/config.py`, `quoter/runner/run_control.py`; extend `tests/test_run_control_cfg.py`.

- [ ] **Step 1: Add Config fields** — in `quoter/config.py`, next to the `tb_*` block, add:

```python
    # ── phase-28 momentum-take strategy (0xb27b decode: chase mover + merge floor) ──
    mom_lookback: float = 30.0       # sec of mid history for the causal momentum signal
    mom_threshold: float = 0.03      # mid move over lookback to trigger a chase
    mom_chase_max: float = 0.95      # never take the mover above this price
    mom_residual_cap: float = 5.0    # max net directional exposure (mover inv - fader inv)/window
```

- [ ] **Step 2: Write the failing cfg-guard test** — append to `tests/test_run_control_cfg.py`:

```python
def test_momentum_cfg_is_dry_run_by_default():
    os.environ.pop("LIVE_GO", None)
    rc = _load_rc("momentum")
    c = rc.CFG
    assert c.dry_run is True               # LIVE DISABLED without LIVE_GO
    assert c.strategy == "momentum"
    assert c.timeframes == ("5m",)
    assert c.tb_size == 5.0
    assert c.mom_residual_cap == 5.0
    assert c.mom_chase_max == 0.95
    assert c.per_window_cap == 15.0


def test_momentum_live_go_lifts_lock():
    os.environ["LIVE_GO"] = "1"
    try:
        assert _load_rc("momentum").CFG.dry_run is False
        assert _load_rc("five_min").CFG.dry_run is True   # others stay locked
    finally:
        os.environ.pop("LIVE_GO", None)
```

- [ ] **Step 3: Run — expect fail** (momentum branch missing): `.venv/bin/python -m pytest tests/test_run_control_cfg.py -q`

- [ ] **Step 4: Add the `momentum` CFG branch** in `quoter/runner/run_control.py`, immediately BEFORE the `elif STRATEGY == "five_min":` branch:

```python
elif STRATEGY == "momentum":
    # 0xb27b decode: taker-chase the momentum side at extremes + cheap fader + merge floor +
    # small capped directional residual, never sell. LIVE only via LIVE_GO=1 (momentum only).
    _LIVE_GO = os.environ.get("LIVE_GO") == "1"
    CFG = Config(
        strategy="momentum", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_tick=0.001, tb_merge_min=1.0, requote_sec=_REQUOTE_SEC,
        mom_lookback=30.0, mom_threshold=0.03, mom_chase_max=0.95, mom_residual_cap=5.0,
        per_window_cap=15.0, per_market_cap_usd=15.0, min_time_to_expiry_sec=5.0,
        dry_run=not _LIVE_GO,
    )
```

Note: `_REQUOTE_SEC` is defined inside the `if STRATEGY == "top_book":` block — lift its
definition (the `try/except` that reads `REQUOTE_SEC`) ABOVE the strategy `if`, or duplicate a
minimal `_REQUOTE_SEC = 2.0` default here. Simplest: add `_REQUOTE_SEC = 2.0` at the top of this
branch. Also update the final assert so `momentum` + `LIVE_GO=1` is allowed to be non-dry-run:

```python
assert CFG.dry_run is True or (
    CFG.strategy in ("top_book", "momentum") and os.environ.get("LIVE_GO") == "1"
), "LIVE TRADING DISABLED: CFG.dry_run must stay True (top_book/momentum live needs LIVE_GO=1)"
```

- [ ] **Step 5: Run — expect PASS** (momentum cfg tests + existing ones): `.venv/bin/python -m pytest tests/test_run_control_cfg.py -q`

- [ ] **Step 6: Commit** — `git add quoter/config.py quoter/runner/run_control.py tests/test_run_control_cfg.py && git commit -m "feat(momentum): config fields + live-locked momentum CFG"`

---

### Task 3: `_momentum_window` tick loop + dispatch (integration test)

**Files:** Modify `quoter/runner/merge_runner.py`; create `tests/test_momentum_window.py`.

- [ ] **Step 1: Add imports** near the other top_book_planner import in `merge_runner.py`:

```python
from quoter.runner.top_book_planner import (
    plan_top_book, diff_quotes, plan_merge, committed_gate, skew_ok, link_pair_bids, taker_fee)
from quoter.research.chase import chase_signal
```

- [ ] **Step 2: Add the dispatch** in `run_forever`, right after the `top_book` branch and before `elif self.cfg.strategy == "five_min":`:

```python
                        elif self.cfg.strategy == "momentum":
                            await self._momentum_window(m, mid)
```

- [ ] **Step 3: Add `_momentum_window`** as a new method on `MergeRunner` (place it right after `_top_book_window`):

```python
    async def _momentum_window(self, m, mid_at_entry: float) -> None:
        """Momentum-take (phase-28, 0xb27b decode): when a side has upward momentum, TAKE it
        (mover=winner) at its ask chasing up to mom_chase_max, and TAKE the fader (loser) cheap
        at its ask, both FOK; merge matched pairs each tick (floor); keep the net-long-mover
        residual capped at mom_residual_cap and ride it to resolution; NEVER sell. Taker spend
        (incl. taker_fee) bounded by per_window_cap. Live-execution glue — operator-gated."""
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        self.state.last_event = f"momentum {m.slug}"
        self.state.fills_window = 0.0
        self.state.matched_pct = 0.0
        log.info("momentum_enter", slug=m.slug, mid=round(mid_at_entry, 3))
        inv = {"Up": 0.0, "Down": 0.0}
        cost = {"Up": 0.0, "Down": 0.0}        # committed taker spend incl fee (never decremented)
        held_cost = {"Up": 0.0, "Down": 0.0}   # cost of shares still held (decremented on merge)
        merged = 0.0
        tok = {"Up": m.yes_token, "Down": m.no_token}
        mid_hist: list[tuple[float, float]] = []
        cadence = self.cfg.requote_sec
        try:
            async with httpx.AsyncClient(timeout=8) as cl:
                while m.time_remaining() > END_BUFFER_SEC and not self._shutdown:
                    if self.state.drain_force_stop():
                        await self.cancel_all()
                        return
                    try:
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": tok["Up"]})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": tok["Down"]})).json()
                    except Exception:
                        await asyncio.sleep(cadence)
                        continue
                    try:
                        bbu = _best(by, "bids")
                        bau = _best(by, "asks")
                        if bbu is not None and bau is not None:
                            mid_hist.append((monotonic(), (bbu + bau) / 2))
                        sig = chase_signal(mid_hist, monotonic(),
                                           self.cfg.mom_lookback, self.cfg.mom_threshold)
                        if sig is not None and not self.cfg.dry_run:
                            mover = sig
                            fader = "Down" if sig == "Up" else "Up"
                            for side in (mover, fader):
                                other = "Down" if side == "Up" else "Up"
                                ask = _best(by if side == "Up" else bn, "asks")
                                if ask is None or ask <= 0 or ask >= 0.99:
                                    continue
                                if side == mover and ask > self.cfg.mom_chase_max:
                                    continue
                                if inv[side] - inv[other] >= self.cfg.mom_residual_cap:
                                    continue
                                unit = self.cfg.tb_size * (ask + taker_fee(ask))
                                if cost["Up"] + cost["Down"] + unit > self.cfg.per_window_cap:
                                    continue
                                r = await self._place_limit(token_id=tok[side], price=ask,
                                                            size=self.cfg.tb_size, side="BUY",
                                                            post_only=False, order_type="FOK")
                                filled = (self._order_matched(r["order_id"])
                                          if (r and r.get("order_id")) else None)
                                if filled:
                                    c = filled * (ask + taker_fee(ask))
                                    inv[side] += filled
                                    cost[side] += c
                                    held_cost[side] += c
                                    log.info("momentum_take", side=side, mover=(side == mover),
                                             price=round(ask, 3), size=filled)
                        # merge floor — recycle capital, lock the pair at $1
                        mq = plan_merge(inv["Up"], inv["Down"], self.cfg.tb_merge_min)
                        if mq > 0 and not self.cfg.dry_run:
                            ok = await self._merge_pairs(m, mq)
                            if ok:
                                for s in ("Up", "Down"):
                                    avg_s = held_cost[s] / inv[s] if inv[s] > 0 else 0.0
                                    held_cost[s] = max(0.0, held_cost[s] - mq * avg_s)
                                    inv[s] -= mq
                                    if inv[s] <= 0:
                                        inv[s] = 0.0
                                        held_cost[s] = 0.0
                                merged += mq
                                self.state.merged_today += mq
                        self.state.pairs_caught = int(min(inv["Up"], inv["Down"]))
                        self.state.naked_shares = int(abs(inv["Up"] - inv["Down"]))
                        self.state.fills_window = inv["Up"] + inv["Down"] + 2 * merged
                        self.state.matched_pct = (100 * 2 * merged
                                                  / max(self.state.fills_window, 1e-9))
                    except Exception as e:
                        log.warning("momentum_tick_err", error=str(e))
                    await asyncio.sleep(cadence)
        finally:
            await self.cancel_all()
        log.info("momentum_done", slug=m.slug, merged=merged,
                 inv_up=inv["Up"], inv_dn=inv["Down"],
                 spent=round(cost["Up"] + cost["Down"], 2))
```

- [ ] **Step 2b: Verify `_best` and `monotonic` are in scope** — `_best` is defined at module level in merge_runner.py (used by top_book); `monotonic` is already imported (used by top_book). No new import beyond Step 1. Confirm by grep: `grep -nE "^def _best|from time import monotonic|import monotonic|monotonic" quoter/runner/merge_runner.py`.

- [ ] **Step 3: Write the integration test** `tests/test_momentum_window.py` (mirror the `_make_runner`/`_run` mock harness from `tests/test_top_book_complete.py`; adapt books so Up's mid RISES tick-over-tick to fire `chase_signal`, and assert):

```python
"""Momentum-take mechanics: on rising-Up momentum the loop TAKES Up (mover) + Down (fader) as
FOK takers, merges pairs, caps the net-long residual at mom_residual_cap, never SELLS, and
bounds taker spend by per_window_cap. Edge is NOT tested here (only measurable live)."""
import asyncio
from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.trading_state import TradingState


class _Ctl:
    def __init__(self):
        self.t_remaining = 100.0
        self.tick = 0
        self.clock = 1000.0
        self.places = []            # (token, side, price, size, post_only, order_type)
        self.seq = 0
        self.kind = {}
        self.size = {}


def _book(bid, ask):
    return {"bids": [{"price": f"{bid:.3f}", "size": "500"}],
            "asks": [{"price": f"{ask:.3f}", "size": "500"}]}


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


class _M:
    slug = "btc-updown-5m-x"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def __init__(self, ctl):
        self._ctl = ctl

    def time_remaining(self):
        return self._ctl.t_remaining


def _make_runner(ctl):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(strategy="momentum", assets=("BTC",), timeframes=("5m",),
                   tb_size=5.0, tb_tick=0.001, tb_merge_min=1.0,
                   mom_lookback=6.0, mom_threshold=0.03, mom_chase_max=0.95,
                   mom_residual_cap=5.0, per_window_cap=15.0, dry_run=False)
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.seq += 1
        oid = f"o{ctl.seq}"
        ctl.kind[oid] = kw.get("order_type")
        ctl.size[oid] = kw["size"]
        ctl.places.append((kw["token_id"], kw["side"], kw["price"], kw["size"],
                           kw.get("post_only"), kw.get("order_type")))
        return {"order_id": oid, "status": "live"}

    async def fake_merge(m, qty):
        return True

    r._place_limit = fake_place
    r._cancel_orders = lambda oids: asyncio.sleep(0)
    r._open_order_ids = lambda: set()
    r._order_matched = lambda oid: ctl.size.get(oid, 0.0)   # FOK fills its own size
    r._merge_pairs = fake_merge
    r.cancel_all = lambda: asyncio.sleep(0)
    return r


def _run(ctl, runner, n_ticks, monkeypatch):
    # Up mid rises each tick: 0.50 -> 0.56 -> 0.62 ... (fires chase_signal("Up")); Down mirrors.
    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            up = 0.50 + 0.06 * ctl.tick
            up = min(up, 0.95)
            if params["token_id"] == "UP":
                return _Resp(_book(up - 0.01, up + 0.01))
            return _Resp(_book((1 - up) - 0.01, (1 - up) + 0.01))

    async def fake_sleep(_):
        ctl.tick += 1
        ctl.clock += 2.0
        if ctl.tick >= n_ticks:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(merge_runner, "monotonic", lambda: ctl.clock)
    asyncio.run(runner._momentum_window(_M(ctl), 0.5))


def _fok(ctl, token):
    return [p for p in ctl.places if p[0] == token and p[1] == "BUY" and p[5] == "FOK"]


def test_takes_mover_and_fader_as_taker(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)
    _run(ctl, runner, 5, monkeypatch)
    assert _fok(ctl, "UP"), "must TAKER-buy the rising mover (Up)"
    assert _fok(ctl, "DN"), "must TAKER-buy the cheap fader (Down)"
    assert all(p[4] is False for p in ctl.places), "all takes are taker (post_only False)"


def test_never_sells(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)
    _run(ctl, runner, 6, monkeypatch)
    assert not any(p[1] == "SELL" for p in ctl.places), "momentum strategy never sells"


def test_residual_capped(monkeypatch):
    ctl = _Ctl()
    runner = _make_runner(ctl)
    _run(ctl, runner, 8, monkeypatch)
    assert runner.state.naked_shares <= 5   # mom_residual_cap
```

- [ ] **Step 4: Run — expect PASS** (adjust the book ramp / n_ticks if the signal needs more history to fire): `.venv/bin/python -m pytest tests/test_momentum_window.py -q`

- [ ] **Step 5: Run the FULL suite** — nothing else broke, top_book untouched: `.venv/bin/python -m pytest -q`
Expected: all pass (was 449 + new tests).

- [ ] **Step 6: Commit** — `git add quoter/runner/merge_runner.py tests/test_momentum_window.py && git commit -m "feat(momentum): _momentum_window taker-chase + merge floor + capped residual, never-sell"`

---

### Task 4: (GATED — separate, on explicit user "go") arm + attended live test

Not a code task — the execution step after Task 3 merges. Do NOT run without the user's explicit
per-instance "go".

- [ ] Deploy the 4 changed files to the server; verify `STRATEGY=momentum LIVE_GO=1` gives
  `dry_run=False`, and default (no LIVE_GO) stays dry-run.
- [ ] Start the watchdog with **dd-stop −$30** and a residual tripwire; fresh baseline.
- [ ] Arm `STRATEGY=momentum LIVE_GO=1`, restart, press START; verify RUNNING.
- [ ] Attended: watch per-window `momentum_take` events (mover px, fader px), `naked_shares`
  (residual), merges, equity. Record realized PnL + whether the chased mover resolved as winner.
- [ ] Stop on the −$30 watchdog breach or an attended call; force_stop; re-lock to dry-run.

## Notes

- The passive `top_book` and `five_min` paths are untouched — regression-guarded by the full suite.
- Edge is UNMEASURABLE offline (toxicity model proof); the live test is a measurement, not an
  expected profit — high variance, regime-dependent, hard-capped at −$30.

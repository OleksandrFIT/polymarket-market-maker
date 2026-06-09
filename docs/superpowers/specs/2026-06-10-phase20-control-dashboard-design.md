# Phase-20: Live Control Dashboard — START / STOP / FORCE STOP merge-maker runner

**Date:** 2026-06-10
**Status:** Design (approach B approved)
**Goal:** A continuously-running merge-maker service the operator controls from a
web dashboard — START/STOP trading without killing the process or the instance.
Reuses the PROVEN two-sided posting + polling logic from `live_strategy_test.py`
(it caught a real hedged pair live). Live, BTC-only, hard caps, paper-safe defaults.

## Why approach B (not full quoter.main)
`live_strategy_test.py` is proven live (posted both legs, caught a pair, polling
fills — no WS). The full `quoter.main` has unverified-for-V2 WS fill handling, on
which the per-market cap depends. So we wrap the proven code in a controlled loop
rather than run the unverified machinery.

---

## 1. Components (new package `quoter/runner/`)

### `trading_state.py` — the control state machine (pure, unit-tested)
```
mode: "STOPPED" | "RUNNING"            # default STOPPED
trade_from_open_ts: int | None         # only enter windows with open_ts > this
force_stop_requested: bool             # one-shot flag drained by the loop
# session stats: pairs_caught, naked_shares, windows_traded, last_window
```
- **START(now_window_open_ts):** mode=RUNNING; `trade_from_open_ts = current
  window open_ts` → the current window is skipped, the NEXT (open_ts greater) is
  the first traded. ("enter from next window")
- **STOP():** mode=STOPPED. The loop stops entering NEW windows. An in-flight
  current-window task is allowed to run to its natural end (graceful — its resting
  legs keep trying to complete the pair; nothing is force-cancelled).
- **FORCE_STOP():** mode=STOPPED + set `force_stop_requested`. The loop cancels
  ALL resting orders immediately (`cancel_all`) and interrupts the current task.
- **Entry rule:** enter a window iff `mode==RUNNING and open_ts > trade_from_open_ts
  and window is fresh (>= FRESH_MIN s left) and mid balanced and not already
  traded this window`.

### `merge_runner.py` — the continuous loop (reuses proven logic)
```
while not shutdown:
    if state.force_stop_requested: cancel_all(); drain flag
    window = discover current fresh BTC 5m window
    if state.should_enter(window):
        await trade_window(window)     # post both legs, poll fills to window end
    await sleep(SHORT)
```
`trade_window`: compute_ladder → post both legs via ClobOps (V2) → poll fills
(conditional balances) until window end **or** force_stop; matched pairs held to
resolution; unfilled legs cancelled at the end (or immediately on force_stop).
Updates session stats. Hard caps: `per_market_cap_usd`, `max_naked_shares`, BTC-only.

### `control_dashboard.py` — aiohttp web UI (reuses the project's dashboard style)
- **Status:** trading mode, current window + time left, windows traded this
  session, pairs caught, naked shares, live cash balance, open-order count.
- **Buttons:** `START` · `STOP` · `FORCE STOP` → POST endpoints mutating the
  TradingState. Auto-refresh ~2 s. Exposed on `127.0.0.1:8080` (SSH-tunnel only,
  never public).

### `run_control.py` (entry point)
Wires creds → ClobOps → TradingState → MergeMakerRunner + ControlDashboard, runs
both forever. This is what runs 24/7 on the server (replacing the one-shot script).

---

## 2. Button semantics (operator-facing)

| Button | New windows | Current window's resting orders | Held pairs |
|---|---|---|---|
| **START** | enter from NEXT window | — | — |
| **STOP** (graceful) | ❌ stop | ✅ keep, finish current window | hold to resolution |
| **FORCE STOP** | ❌ stop | ❌ cancel_all immediately | hold to resolution |

Caught pairs are never sold/cancelled (only unfilled orders are). FORCE STOP may
leave a naked leg if one side filled and its partner's resting order is cancelled
— that's the accepted emergency tradeoff; it then resolves directionally.

---

## 3. Safety
- Default **STOPPED** on startup (never auto-trades).
- `per_market_cap_usd` (≈$5 test), `max_naked_shares`, BTC-only, one window at a time.
- `RiskGuard` daily-loss halt still applies; FORCE STOP = instant `cancel_all`.
- Dashboard bound to localhost only (SSH tunnel).

## 4. Testing
- `trading_state.py`: unit tests — START skips current/enters next; STOP blocks
  new entry; FORCE_STOP sets flag; entry-rule truth table (mode, open_ts vs
  trade_from, freshness, balance, already-traded).
- `control_dashboard.py`: endpoint tests (GET status, POST start/stop/force) via
  the existing aiohttp TestClient pattern.
- The live posting path (`trade_window`) is the already-proven `live_strategy_test`
  logic; verified live, not unit-tested.
- Full suite green.

## 5. Out of scope (later)
- Auto-redeem (manual on the site for now), on-chain merge, multi-asset, bigger size.
- Real-time WS fills (polling is sufficient and proven).

## 6. Sources
- Proven live logic: `scripts/live_strategy_test.py` (caught a hedged pair live,
  +$0.05, 0 naked, from ca-central-1).
- Existing dashboard/style: `quoter/ops/dashboard.py`, `quoter/ops/metrics.py`.
- Execution: `quoter/execution/clob_client.py` (V2).

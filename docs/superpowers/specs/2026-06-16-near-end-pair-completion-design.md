# Near-End Pair Completion — Design

**Date:** 2026-06-16
**Status:** approved (design), pending spec review

## Goal

Stop riding a naked leg to resolution. Near the window end, COMPLETE the pair
when it can be locked for < $1, otherwise SELL the naked leg to cap the loss —
exactly what the profitable competitor (`l5Zn1bWoM8eTsK`, +$22.5k on-chain) does.
Make the trading timeframe config-driven so we can run 15m as well as 5m.
Both changes are opt-in and must not alter current behavior by default.

## Background / evidence (why this is the right fix)

Real on-chain data (not the proxy backtests, which over-state fills):
- **Our pair pricing is competitive.** Weighted pair cost: us $0.941 / guru $0.938
  (both ≈ +$0.06/pair). Pricing is NOT the problem.
- **The naked leg is the loss.** On 5m, when only one side dips it is the
  structural loser (won 0/53 windows live+history) → ride to resolution = −$2.30.
- **The deployed bot never completes.** The completion/sell block in
  `_ladder_window` is gated `if self.cfg.auto_flat and inv_ok:` — with the live
  config `auto_flat=False` it never runs, so the bot ALWAYS rides naked.
- **The guru's exact rule (from his per-second trades):** mid-window the pair
  sums to ≥ $1 (no arb, he does NOT complete then). The pair only drops < $1
  LATE, at the extremes (loser ≈ 0.02–0.10, favorite ≈ 0.90–0.99). He completes
  by buying the now-cheap leg near the end → locks the pair < $1. Completing
  mid-window (sum ≈ $1) would just pay the taker spread for no edge.

Conclusion: complete ONLY when `heavy_avg + light_ask < 1.0`, and only near the
end (where that condition is naturally satisfied); otherwise SELL the loser
early. Never ride naked.

## Honest caveat

Offline backtests cannot price this fix (they cannot model our queue/latency —
proven repeatedly this project). The fix is correct *by construction* (locking a
< $1 pair beats riding a 0/53 loser) and matches the guru's real behavior, but
the realized edge can only be confirmed by a small live run. This spec makes the
behavior available and safe to test; it does not claim a backtested profit.

## The rule

At each ladder tick, when we hold a naked leg (`inv_yes != inv_no`) and inventory
is known (`inv_ok`):

- **If `complete_pairs` is enabled** and `time_remaining <= complete_gate_sec`
  (the late window):
  - `plan_naked_action(...)` returns COMPLETE when `heavy_avg + light_ask < 1.0`,
    else SELL (existing, already unit-tested logic).
  - COMPLETE → FOK-BUY the light leg at its ask (lock the pair < $1).
  - SELL (or a COMPLETE that could not fill) → FOK-SELL the heavy (loser) leg at
    its bid to cap the loss. Suppress that side for the rest of the window.
- **Before the gate:** do nothing here — let the maker ladder keep accumulating
  cheap legs (do NOT taker-complete mid-window; sum ≈ $1, no edge).
- **If `auto_flat` is enabled instead:** unchanged legacy trigger
  (`naked >= naked_cap` and (`grace` or `near_end`)). Kept for backward compat.

These two modes are mutually exclusive in practice (we will run
`complete_pairs=True, auto_flat=False`).

## Config changes (`quoter/config.py`)

```python
complete_pairs: bool = False        # near-end COMPLETE(<$1)/SELL; never ride naked
complete_gate_sec: float = 60.0     # act only in the last N seconds of the window
```

Defaults keep current behavior (both completion paths off ⇒ ride, identical to
today). `timeframes` stays `("5m",)` by default.

## Code changes

### 1. `quoter/runner/merge_runner.py` — timeframe from config (line ~150)
`discover_markets(Config(assets=("BTC",), timeframes=("5m",)), ...)` →
use `self.cfg.timeframes` (and `self.cfg.assets`) so 15m is selectable via config.
Window length is already dynamic via `m.time_remaining()` — no other change needed.

### 2. `quoter/runner/merge_runner.py` — completion gate (lines ~444-488)
Change the block guard from `if self.cfg.auto_flat and inv_ok:` to
`if (self.cfg.auto_flat or self.cfg.complete_pairs) and inv_ok:` and branch the
trigger:

```python
naked = inv_yes - inv_no
heavy = "YES" if naked > 0 else ("NO" if naked < 0 else None)
for s in ("YES", "NO"):
    if s != heavy:
        naked_since[s] = None
if heavy and heavy not in flattened:
    if self.cfg.complete_pairs:
        trigger = m.time_remaining() <= self.cfg.complete_gate_sec
    else:  # legacy auto_flat
        if abs(naked) >= self.cfg.naked_cap:
            if naked_since[heavy] is None:
                naked_since[heavy] = now
            trigger = ((now - naked_since[heavy]) >= self.cfg.flatten_grace_sec
                       or m.time_remaining() <= self.cfg.flatten_grace_sec)
        else:
            trigger = False
    near_end = m.time_remaining() <= max(self.cfg.flatten_grace_sec,
                                         self.cfg.complete_gate_sec)
    if trigger:
        a = plan_naked_action(inv_yes, inv_no, inv.avg("YES"), inv.avg("NO"),
                              yes_ask, no_ask, self.cfg.naked_cap)
        # ... existing COMPLETE (FOK BUY light) / SELL (FOK SELL heavy) execution,
        #     unchanged ...
```

The COMPLETE/SELL execution bodies (FOK orders, `flattened.add`, logging) are
reused verbatim. `naked_since` bookkeeping only matters for the legacy path.

### 3. `quoter/runner/run_control.py`
Leave defaults for now (operator sets `complete_pairs=True`, `complete_gate_sec`,
and `timeframes` when we launch the live test). No behavior change until set.

## Non-goals / what stays intact

- The maker ladder, pair-catching, `plan_ladder`, `LocalInventory`, the lag-proof
  `posted` backstop, the trend detector, and the legacy `auto_flat` path are all
  unchanged.
- No deletion of 5m support; 15m is additive via config.
- All 262 existing tests must still pass.

## Testing (TDD)

`plan_naked_action` already has unit coverage (COMPLETE < $1 / SELL ≥ $1). Add:

1. **Config defaults test** — `complete_pairs is False`, `complete_gate_sec == 60.0`;
   with defaults the completion block is inert (no behavior change).
2. **Trigger-timing unit** — a small pure helper or extracted function:
   - `complete_pairs=True`, `time_remaining > complete_gate_sec` → no action.
   - `complete_pairs=True`, `time_remaining <= complete_gate_sec`, pair < $1 →
     COMPLETE the light side.
   - same, pair ≥ $1 → SELL the heavy side.
3. **Legacy auto_flat unchanged** — existing auto_flat tests still pass byte-for-byte.
4. **Discovery uses config timeframe** — `discover_markets` is called with
   `self.cfg.timeframes` (assert via the call, not network).

If the trigger logic is hard to test inside the async loop, extract a pure
`decide_naked_trigger(cfg, naked, time_remaining, naked_since, now) -> bool` and
unit-test that; the loop calls it.

## Rollout

1. Implement via TDD + spec/code review (subagent-driven), full suite green.
2. Commit on master (no push).
3. Deploy to server STOPPED; operator sets `complete_pairs=True`,
   `complete_gate_sec`, timeframe; small attended live run (operator-gated, "go").
4. Measure realized pair cost + completion rate live vs the guru's $0.94 / 97%.

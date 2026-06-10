# Phase-21: Continuous Re-Quoting (active two-sided market making)

**Date:** 2026-06-10
**Status:** Design (approved: build + max tests, NO live)
**Goal:** Replace the static "post once and wait" with **continuous re-quoting** —
actively keep our bids at the top of the book on BOTH sides throughout the window
so both legs fill reliably (more matched pairs, less naked-leg time). Built and
**exhaustively tested in simulation**; NOT run live in this phase.

## Why
Live observation (phase-20): a single static post catches one leg fast but the
second leg lags 2–3 min (stale price, not top-of-book). Re-quoting fixes this by
constantly cancel/replacing to stay at the current best bid on both sides.

## Core idea — split into a PURE brain + thin live wrapper + a SIMULATOR
The hard part is decidable without any network: "given the current book, our
resting orders, and our inventory, what should we cancel and post?" That is a
**pure function** → unit-testable to death. The live wrapper just executes the
plan; the simulator drives the brain through synthetic markets to prove behaviour.

---

## 1. `requote_planner.py` — the pure brain (no I/O)

```
plan_requote(*, yes_bid, no_bid, yes_ask, no_ask,
             inv_yes, inv_no, yes_cost, no_cost,
             resting,            # {"YES": RestingOrder|None, "NO": RestingOrder|None}
             cfg) -> RequotePlan  # (cancels: list[order_id], posts: list[Quote])
```
Per tick:
1. **Capital gate:** if `yes_cost + no_cost >= per_market_cap_usd` → cancel all
   resting, post nothing.
2. **Desired price (top of book):** `yes_px = round(yes_bid, 2)`, `no_px =
   round(no_bid, 2)` (join best bid — already maker; sum of best bids < $1 by
   measurement). **EDGE gate:** if `yes_px + no_px >= 1.0` → skip that tick (post
   nothing new, keep/cancel as below).
3. **Balance gate (the key safety):** `naked = inv_yes - inv_no`.
   `want_yes = naked < max_naked_shares` (don't add to a side we're already long
   of past the cap); `want_no = -naked < max_naked_shares`. Only the short/needed
   side(s) are quoted → naked is actively driven toward zero, never past the cap.
4. **Diff vs resting:** for each side we want: if no resting order, or its price
   != desired → `cancel` the stale one and `post` a fresh `Quote(side, px,
   flat_size)`. If a side is NOT wanted → cancel any resting order there.
5. Output the (cancels, posts) plan. Buy-only, post_only.

Pure, deterministic, no awaits. Every branch unit-tested.

## 2. `requote_sim.py` (test-only) — deterministic market simulator
Drives `plan_requote` through synthetic scenarios, applying a simple fill model:
- A book with moving `yes_bid/no_bid` (scripted paths: flat, drifting, choppy).
- Taker arrivals: scripted "a seller hits the best bid on side X this tick" events.
- Each tick: run planner → apply cancels/posts to a virtual order set → apply any
  taker that crosses our resting bid → update inventory.
Used only by tests to assert end-to-end re-quoting behaviour.

## 3. Simulation tests (`tests/test_requote.py`) — the heart of "max testing"
Pure planner unit tests + full-window simulations asserting INVARIANTS:
| Test | Asserts |
|---|---|
| plan: posts both sides at best bid when balanced | both YES & NO in posts |
| plan: edge gate | yes_bid+no_bid ≥ $1 → no new posts |
| plan: balance gate suppresses long side | inv_yes−inv_no ≥ cap → no YES post |
| plan: capital gate | cost ≥ cap → cancels all, posts nothing |
| plan: re-quote on price move | resting at stale price → cancel+repost at new bid |
| plan: keep order when price unchanged | desired==resting → no churn |
| **sim invariant: naked never exceeds max_naked_shares** | over a full window, any flow |
| **sim invariant: matched pairs always cost < $1** | every matched pair |
| **sim invariant: spend never exceeds per_market_cap_usd** | end of window |
| sim: balanced two-way flow → catches ≥N pairs | re-quoting works |
| sim: one-sided flow → stays balanced (caps naked), no runaway | safety under bad flow |
| sim: choppy/moving book → still catches pairs, no self-cross | robustness |

Goal: prove **in pure simulation** that re-quoting (a) catches more pairs than a
static post under the same flow, and (b) NEVER violates the naked/capital caps,
under good AND adversarial flow.

## 4. Wire into the runner (behind a flag, still NO live run)
`merge_runner.trade_window` gains a `requote=True` mode: instead of one post +
passive poll, it loops every `REQUOTE_SEC` (~1–2s): read book + live inventory
(`_shares`), call `plan_requote`, execute cancels/posts via `ClobOps`. Same hard
caps, same STOP (graceful finishes window) / FORCE STOP (cancel_all). The static
mode stays available. **This phase does not press START / trade live** — only
builds + tests the logic.

## 5. Safety (unchanged ceilings)
`per_market_cap_usd` and `max_naked_shares` bound the per-window worst case
EXACTLY as today — re-quoting changes fill *frequency*, not the ceiling. The
balance gate is enforced every tick (not once), so re-quoting drives naked toward
zero rather than letting it sit.

## 6. Out of scope
- Live run (separate, operator-gated decision — not this phase).
- On-chain merge, auto-redeem, multi-asset, size scaling.

## 7. Sources
- Live finding: phase-20 run (second leg lagged 2–3 min with a static post).
- Reused: `compute_ladder` edge/balance concepts; `ClobOps` (V2); the runner/state.

# Sweep-Proof Inventory (optimistic bound + real-fills phantom-kill) — Design

**Date:** 2026-06-13
**Status:** Approved (design), pending implementation
**Supersedes:** the inventory source from 2026-06-13-real-fills-and-complete-pair (which fixed
phantom fills but RE-OPENED the sweep).

## The two failure modes (both observed live)

1. **Dump-sweep (today, window 1781365800, naked 25 vs cap 5).** The real-fills inventory
   (data-api) LAGS several seconds. During a fast one-sided dump, our rungs filled faster than
   the feed reported them. Each tick the bot saw naked ≈ 0 (fills still "in flight"), so the cap
   never pulled the heavy side and the staged `nd` offset re-posted the same rung → 25 swept in
   38s. Inventory that lags can NEVER bound naked under a fast dump.

2. **Phantom-fill (prior test, window 1781302200, "naked 0" but really naked 10).** The OLD
   vanish heuristic ("our order disappeared ⇒ filled") over-credited a side whose orders
   vanished WITHOUT filling (unreliable open-orders read) → bot believed it was balanced and
   over-bought the other side; the flatten was blinded.

The crux: an **immediate** count (vanish ⇒ credit) is needed to bound posting (fixes #1) but
it over-credits phantoms (#2); a **real-fills** count is truthful (fixes #2) but lags (#1).
Neither alone works. We need both, combined correctly.

## Design: one inventory, immediate-credit + real-fills reconciliation

`LocalInventory` (already exists, used pre-real-fills) is the single source for the ladder
loop again, with one new method:

- `credit_fill(side, size, price)` — **immediate** optimistic credit when our resting order
  vanishes uncancelled. This is the SWEEP BOUND: naked rises the instant a rung fills, so the
  cap pulls the heavy side before the next post. Lag-proof because it does not wait for any feed.
- `reconcile_up(side, real_qty, est_price)` — raise local to real when real is higher (catch
  fills/positions we didn't account). (Existing.)
- **NEW** `reconcile_down(side, real_qty, now, grace)` — the PHANTOM KILL. If local > real for
  a side, start a timer; once the gap has PERSISTED ≥ `grace` seconds, lower local to real
  (the optimistic credit was never confirmed by real fills → it was a phantom). A genuine fill
  shows up in real within the feed lag, so a real fill clears the gap before `grace` and is
  never wrongly reversed. `grace` MUST exceed the data-api feed lag.

Each tick: credit vanished rungs (immediate) → `reconcile_up` and `reconcile_down` against the
real-fills inventory (`fetch_window_fills` + `inventory_from_fills`) → the reconciled
`LocalInventory` drives BOTH `plan_ladder` (posting/cap) AND `plan_naked_action` (COMPLETE/SELL).

**Resting coherence (the other half of the sweep bug):** restore removing a vanished order
from `resting` the instant it disappears (the real-fills rewrite dropped this, leaving stale
rungs that confused the `nd`/`yd` staging). `resting` must reflect only live orders.

### Why this fixes both

- **Dump:** `credit_fill` is immediate → optimistic naked hits the cap after the first rung →
  `plan_ladder` stops posting the heavy side → bounded at `cap + at most one in-flight rung`,
  regardless of how far the data-api lags. (This is exactly the pre-real-fills behavior, which
  was sweep-proof; we are restoring it.)
- **Phantom:** if a vanish was NOT a real fill, real-fills never confirm it; after `grace`,
  `reconcile_down` lowers local to real → the hidden naked is revealed → the flatten acts.
- **Residual (phantom masks the other side during `grace`):** while a phantom credit stands,
  the bot may over-post the OTHER side. This is bounded by `grace` and is why `grace` is set
  just above the measured feed lag (not arbitrarily long). The lag-sim quantifies this bound.

### Action/posting both use the reconciled local

`plan_naked_action` is fed `inv.avg(...)` and naked from the reconciled `LocalInventory`. Real
fills still drive `reconcile_*`. `fetch_window_fills`/`inventory_from_fills` stay as the real
source; they are no longer the DIRECT posting inventory.

## THE CENTERPIECE: a lag-modeling simulation (test-first)

My two prior fixes failed because the sims assumed instant, perfect inventory. This sim models
the real defect:

`tests/test_sweep_sim.py` — a `LagSim` that models:
- a **delayed real-fills feed**: a fill at tick T enters `real_fills` at tick `T + feed_lag`;
- **vanish-detection**: a filled rung leaves "open orders" at tick T (so `credit_fill` sees it
  immediately) and is removed from `resting`;
- an optional **phantom**: a posted order that vanishes at tick T but NEVER enters `real_fills`
  (models the unreliable open-orders read);
- the loop: read inventory → `plan_ladder` desired → post → scripted takers fill.

Tests (must FAIL on the current real-fills-only inventory, PASS after the fix):
1. `test_dump_sweep_reproduced_then_bounded`: a fast one-sided dump (NO fills every tick) with
   `feed_lag=4`. With inventory = lagged-real-only → naked >> cap (reproduce ~the 25). With the
   fixed optimistic+reconcile inventory → `max_naked <= naked_cap + rung_size`.
2. `test_phantom_corrected`: inject a phantom NO vanish (never in real_fills). After `grace`
   ticks, `reconcile_down` lowers local to real; the hidden YES naked is revealed (so the gate
   would flatten it). Assert local converges to real.
3. `test_real_fill_not_wrongly_reversed`: a genuine fill (enters real_fills after `feed_lag <
   grace`) is NEVER reversed by `reconcile_down` (local stays credited).

The fix is not wired live until tests 1-3 are green.

## Components

| Unit | File | Change |
|---|---|---|
| `LocalInventory.reconcile_down(side, real_qty, now, grace)` + `_over_since` timer | `quoter/runner/local_inventory.py` | NEW method |
| `LagSim` + 3 sweep tests | `tests/test_sweep_sim.py` | NEW |
| live wiring | `quoter/runner/merge_runner._ladder_window` | restore vanish-detection (remove from `resting` + `credit_fill`); add `reconcile_up`/`reconcile_down` vs real-fills; feed reconciled `LocalInventory` to `plan_ladder` + `plan_naked_action` |
| config | `quoter/config.py` | NEW `inv_reconcile_grace_sec: float = 12.0` (> feed lag) |

`fetch_window_fills`/`inventory_from_fills`/`plan_naked_action` are unchanged (reused).

## Safety

- Optimistic `credit_fill` only ever RAISES naked immediately → posting is bounded
  immediately → sweep impossible regardless of feed lag (the property the dump test proves).
- `reconcile_down` only fires after `grace` (> feed lag) → never reverses a real fill.
- Posting still also gated by `per_window_cap` and `max_inflight_rungs=1`.
- `auto_flat=False` path unaffected. Operator-gated; bot STOPPED; no live until the sim is green
  AND a fresh code review passes.

## Operational

Work on `master`, local + tests only. NO live run until: (a) the 3 lag-sim tests green, (b) full
suite green, (c) code review APPROVED, (d) re-deploy + the data-api shape re-validated, and (e)
the user's explicit "go". Server <SERVER_IP>, bot STOPPED, cash ~$73.22.

## Out of scope (YAGNI)

- Measuring exact data-api lag (set `grace=12s`, comfortably above observed; sim shows the
  residual bound). - WebSocket user-channel fills. - Touching legacy `_requote_window`.

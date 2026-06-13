# Real-Fills Inventory + Complete-Pair Flatten — Design

**Date:** 2026-06-13
**Status:** Approved (design), pending implementation
**Spec supersedes parts of:** 2026-06-12-auto-flat-design.md (the flatten action + the inventory source)

## Why

The 2026-06-12 live auto-flat test (3 windows, realized −$3.50) surfaced two real defects:

1. **Phantom fills (critical, window 2, −$3.15).** The live loop infers a fill from "our
   resting order vanished from the open-orders list." An unreliable open-orders read (an
   empty/partial response) made the bot credit 10 Down fills that never happened. It then
   believed it held 10 pairs (naked 0) when it actually held 10 naked Up. The auto-flat was
   blinded (it only acts on naked it can see), the naked rode to resolution, Down won → −$3.15.

2. **Flatten breaks would-be pairs (window 3, −$0.95).** The two legs of a pair filled ~25s
   apart. The 20s grace on the first (Up) leg expired before the Down leg arrived, so the bot
   sold the Up leg and suppressed Up. When Down then filled, there was no Up to pair it; Down
   rode naked and was flattened at a crash price. A `0.61 + 0.36 = 0.97` pair that would have
   paid +$0.15 became −$0.95.

The pairs themselves work (window 1: +$0.60 clean). These two fixes make the inventory
truthful and the naked-handling pair-seeking.

## Fix 1 — inventory from REAL fills (replaces the vanish heuristic)

**Principle:** never infer a fill from "the order disappeared." Read the actual matched
trades from the authenticated CLOB trades API — the same ground truth used to reconstruct
PnL after the test (it correctly showed 10 Up / 0 Down for window 2).

- New `clob_client` method `recent_fills(market: str, after_ts: int) -> list[dict]` wrapping
  `get_trades(TradeParams(market=<condition_id>, after=<window_open_ts>))`. Each returned
  fill is normalized to `{"token": str, "side": "BUY"|"SELL", "size": int, "price": float}`.
  (TradeParams fields confirmed: `id, maker_address, market, asset_id, before, after`.)
- New pure `quoter/runner/fill_inventory.py` `inventory_from_fills(fills, yes_token, no_token)
  -> Inventory` where `Inventory` carries `inv: {"YES","NO"}`, `cost: {"YES","NO"}`:
  - `net_qty[side] = Σ BUY size − Σ SELL size` for that side's token.
  - `cost[side] = Σ(BUY size × price)` for that side (buy cost basis; sells reduce qty, not
    the recorded buy-cost — `avg` below divides by net_qty so the basis of *held* shares is
    the buy average, which is what `pair_ok` needs).
  - `avg(side) = cost[side] / buy_qty[side]` (average BUY price), or None if no buys.
  - This is a STATELESS rebuild each tick from the full window's fills — no drift, no
    phantom, no lag-guessing. Self-corrects automatically as the trades API settles.
- The live `_ladder_window` loop replaces the whole credit-on-vanish + `LocalInventory` +
  `reconcile_up` block with: `fills = await self.clob.recent_fills(m.market_id,
  m.open_ts); inv = inventory_from_fills(fills, m.yes_token, m.no_token)`. `inv_yes/inv_no/
  cost` come from `inv`. The `resting`/`placed_at` tracking stays ONLY to know what is open
  (for cancel/repost diffing) — it no longer feeds inventory.
- `LocalInventory` (`credit_fill`/`debit_fill`/`reconcile_up`) is retired from the ladder
  loop. Keep the file (the legacy `_requote_window` still uses it) but the ladder path no
  longer imports it. `debit_fill` is no longer needed for the ladder (sells are reflected by
  the trades API like any other fill).

**Latency note (honest):** the trades API can lag a fill by a fraction of a second (faster
than on-chain settlement, slower than the local optimistic credit). During that sliver the
loop might briefly not see a just-filled rung. This is bounded by `max_inflight_rungs=1` (at
most one extra rung) and the naked cap. We accept a hair more latency for correctness — the
optimistic credit's whole failure mode was over-confidence, which cost −$3.15.

## Fix 2 — complete the pair before flattening

**Principle:** when a leg goes naked, prefer to COMPLETE the pair (buy the missing side) over
selling, whenever the completed pair would still cost < $1. Only SELL when completion is too
expensive or has no liquidity.

Replace `plan_flatten` with `plan_naked_action(inv_yes, inv_no, yes_avg, no_avg, yes_ask,
no_ask, naked_cap) -> NakedAction | None`:

- `naked = inv_yes − inv_no`; if `|naked| < naked_cap` → `None`.
- Heavy side = the side with excess (`YES` if naked>0 else `NO`); light side = the other.
- `qty = |naked|`. `heavy_avg` = avg buy price of the heavy side.
- `light_ask` = current ask of the light side (what completion would cost as a taker).
- If `light_ask` is a valid price and `heavy_avg + light_ask < 1.0`:
  → `NakedAction(kind="COMPLETE", side=<light>, qty=qty, price=light_ask)`
- Else:
  → `NakedAction(kind="SELL", side=<heavy>, qty=qty, price=<heavy bid>)`
- (`price` is filled by the caller from live book; the pure function takes asks/avgs to
  decide kind and returns the side+qty. Keep it pure by passing the needed prices in.)

Live handling in `_ladder_window` (after the grace gate fires):
- `COMPLETE`: `place_limit(token=light, price=light_ask, size=qty, side="BUY",
  post_only=False, order_type="FOK")`. On success the next tick's `recent_fills` shows the
  buy → inventory balances → naked gone, pair held to resolution. Log `runner_ladder_complete`.
- `SELL`: `place_limit(token=heavy, price=heavy_bid, size=qty, side="SELL",
  post_only=False, order_type="FOK")` (unchanged flatten). Log `runner_ladder_flatten`.

## Fix 3 — suppress only after a SELL

Suppression (`flattened` set → `plan_ladder(suppressed=...)`) is added **only** on a `SELL`
action — a genuine trend exit where we want to stop loading the loser. A `COMPLETE` does NOT
suppress: we balanced into a pair and normal laddering may continue. This removes the window-3
failure where suppressing the first leg killed the pair.

## Grace + backstop (unchanged from auto-flat spec)

The persistence gate stays: act only when `|naked| >= naked_cap` has stood for
`flatten_grace_sec` (20s), or `near_end = time_remaining <= flatten_grace_sec` (backstop).
With Fix 2, the action at that point is COMPLETE-or-SELL rather than always SELL.

## Components

| Unit | File | Responsibility |
|---|---|---|
| `recent_fills(market, after_ts)` | `quoter/execution/clob_client.py` | fetch + normalize our window fills via `get_trades` |
| `inventory_from_fills(fills, yes_token, no_token)` | new `quoter/runner/fill_inventory.py` | pure: fills → net qty + buy-cost + avg per side |
| `plan_naked_action(...)` | `quoter/runner/flatten_planner.py` (replaces `plan_flatten`) | pure: COMPLETE vs SELL vs None |
| live wiring | `quoter/runner/merge_runner._ladder_window` | use real-fill inventory; COMPLETE/SELL; suppress only on SELL |

## Safety

- Inventory is ground truth (trades API) → no phantom, no over-credit.
- COMPLETE only when `heavy_avg + light_ask < 1.0` → the assembled pair is guaranteed
  profitable-or-flat at resolution (pays $1 for < $1 cost). Never completes into a > $1 pair.
- SELL/COMPLETE both FOK → full fill or nothing (no partial-fill untracked inventory).
- COMPLETE/SELL price taken from the live book; if the needed ask/bid is missing → skip this
  tick, retry next (naked stays bounded by the cap).
- `auto_flat=False` → none of this runs; legacy path unchanged.
- If `recent_fills` errors → reuse last good inventory for the tick and skip new posts (do not
  fall back to the vanish heuristic). Never trade on an unknown inventory.

## Testing

- `inventory_from_fills`: buys only; buys+sells (flatten reflected); two-sided; net/avg/cost
  correctness; window-2 case (10 Up buys, 0 Down → naked 10, NOT 0).
- `plan_naked_action`: below cap → None; cheap other side → COMPLETE(light, qty); expensive
  other side (pair ≥ $1) → SELL(heavy, qty); missing ask → SELL fallback.
- `recent_fills`: normalization shape (mock the V2 client response) — light, I/O-glue style.
- Ladder sim: extend to drive `plan_naked_action` + completion; prove window-3 scenario
  (5 Up @0.61, then 5 Down available @0.39) → COMPLETE → pair held, no churn loss; and a
  trend scenario (other side too expensive) → SELL + suppress.

## Operational

Work on `master`. Local code + unit tests only — NEVER launch live (operator-gated, AWS
ca-central-1, user "go"). Deploy waits until the server (currently SSH-unreachable, bot
confirmed STOPPED before the drop) is back: rsync + restart `poly-control` STOPPED.

## Out of scope (YAGNI)

- WebSocket user-channel fills (trades-API poll is enough at this size/cadence).
- Reworking the legacy `_requote_window` (single-bid path) — untouched.

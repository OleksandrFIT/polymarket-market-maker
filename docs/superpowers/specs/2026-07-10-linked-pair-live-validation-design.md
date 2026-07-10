# Linked-Pair Live Validation — Design Spec

**Goal:** Instrument the `top_book` window with realized pair-cost telemetry so a small gated
live run can answer the ONE question no offline test can: does linked-pair quoting actually
assemble matched pairs at cost < $1 on live books (like 0xb27b's ~$0.98), or does async /
adverse-selection still push the realized pair ≥ $1 at our execution speed?

**Status:** New instrumentation on the existing `top_book` merge-maker path (untouched otherwise).
The live run itself is a SEPARATE, operator-gated step (explicit per-instance "go", server-only) —
this spec delivers the measurement machinery, not the live trade.

---

## Background — why this is the only unfinished code step

All prior analysis converged (see [[project-momentum-edge]], [[project-poly-quoter-status]]):
0xb27b's edge = BTC-5m matched-pair merge (+1.8%/pair, pair VWAP ~0.98) at a ~23:1 match:naked
ratio × hundreds of windows × scale. The MECHANISM is already in the bot (`top_book` neutral mode:
linked-pair quoting, continuous merge, continuous completion, sell-naked fallback). The 23:1 ratio
and scale are CAPITAL, not code — not buildable at size 5. The taker replications (v8/v9) were
proven −EV; the neutral maker lost −$9.54 live to adverse selection BEFORE linked-pair quoting
(`tb_link_margin`) was added to fix exactly that async-fill mechanism.

Linked-pair caps the light-side bid at `1 − heavy_avg − margin` so any pairing fill is < $1 by
construction. **Offline cannot verify this** — tape reconstruction can't price pairs (last-trade
sums exceed $1, the async artifact that made every offline backtest non-decision-grade). The only
proof is a live measurement of the REALIZED pair cost. Today the bot logs `topbook_done`
(merged, inv, spent, committed) but NOT the realized pair cost of the merged pairs, so a live run
is not yet decision-grade. This spec closes that gap.

## Non-goals (YAGNI)

- Do NOT change quoting, completion, merge, or sell logic. Instrumentation only.
- Do NOT touch `_ladder_window`, `_momentum_window`, `_five_min_window`, or the passive planner.
- No dashboard, no parallel dry/live logger harness (rejected approach B).
- No auto-launch of any live run. No scale-up. No claim of profitability — this is a MEASUREMENT.

## Architecture

One new accumulator + one new structured log event in `_top_book_window`
(`quoter/runner/merge_runner.py`), plus a pure post-run aggregator script. The window already
tracks `held_cost` per side and `merged`; it decrements `held_cost` on each merge but never
accumulates the merged pairs' cost basis. We add that accumulation and emit it.

### Unit 1 — realized pair-cost accumulator (in `_top_book_window`)

At window start, alongside `merged = 0.0`, add `merged_cost = 0.0` and counters
`completes = 0`, `sells = 0`.

At EACH merge (current merge block ~line 1052–1066, where `held_cost[s]` is decremented by
`mq * avg_s`): before decrementing, capture the two sides' held averages and accumulate the pair
cost basis:

```python
avg_up = held_cost["Up"] / inv["Up"] if inv["Up"] > 0 else 0.0
avg_dn = held_cost["Down"] / inv["Down"] if inv["Down"] > 0 else 0.0
merged_cost += mq * (avg_up + avg_dn)          # realized $ paid for these mq pairs
```

This is the cost basis of the mq pairs merged this tick, computed from the SAME held-avg the
existing decrement uses, so it is consistent with the accounting already there. `pair_cost` at
window end = `merged_cost / merged` (None if `merged == 0`).

Increment `completes += 1` where `topbook_complete` is logged (~line 1027) and `sells += 1`
where `topbook_sell_naked` is logged (~line 1050) — cheap counters for the fillquality summary.

### Unit 2 — `topbook_fillquality` window-summary event

Immediately before (or merged into) the existing `topbook_done` log (~line 1086), emit:

```python
naked = inv["Up"] - inv["Down"]                # signed residual after final merges
win = "Up" if mid_at_last >= 0.5 else "Down"   # last observed mid (already tracked for topbook)
resid_outcome = ("flat" if abs(naked) < 1e-9
                 else "WON" if (naked > 0) == (win == "Up") else "LOST")
match_naked = (merged / abs(naked)) if abs(naked) >= 1e-9 else None   # None = fully paired
log.info("topbook_fillquality", slug=m.slug,
         pair_cost=round(merged_cost / merged, 4) if merged > 0 else None,
         pairs_merged=merged,
         naked_resid=round(naked, 1),
         resid_outcome=resid_outcome,
         match_naked=round(match_naked, 1) if match_naked is not None else None,
         completes=completes, sells=sells,
         spent=round(cost["Up"] + cost["Down"], 2))
```

`mid_at_last`: the window already updates a mid each tick for quoting; reuse the last value it
holds (if none is retained, fall back to `mid_at_entry`). No new market fetch.

### Unit 3 — `scripts/_pairquality.py` (pure aggregator)

Reads a control.log (path arg), greps `topbook_fillquality` lines (JSON), computes across windows:
- n windows, mean & median `pair_cost`, **% of windows with pair_cost < 1.0** (the headline metric),
- median `match_naked`, distribution of `resid_outcome` (WON/LOST/flat counts),
- total completes / sells.
Prints a decision-grade verdict block. No network, no state — a stdin/file parser like the
existing `analysis_data` scripts.

## Parameters (the gated live run — for reference, NOT set by this spec)

The live run is a later operator step. Documented target config (already the top_book neutral
defaults in `run_control.py`): `STRATEGY=top_book REGIME_GATE=0 LIVE_GO=1`, `tb_size=5`,
`tb_naked_cap=12`, `tb_link_margin=0.01`, `per_window_cap=15`, watchdog equity dd-stop.
Neutral (every window) is required: linked-pair only faces a real test on non-calm windows; calm
windows pair trivially and would pass vacuously. ~15–20 windows for a usable pair-cost distribution.

## Decision criterion

- **pair_cost mean ≤ 0.99 AND > 70% of windows < $1.00** → linked-pair holds live; the per-pair
  mechanism is +EV and SCALE is the only remaining lever. Linked-pair VALIDATED.
- **pair_cost ≥ 1.00 / < 70% of windows < $1** → async/adverse still beats our execution;
  linked-pair does not rescue it at our speed → the `top_book` ceiling is confirmed with no
  remaining "what if", branch closed definitively.

## Safety

- Instrumentation is log-only: cannot change orders, inventory, or PnL. Default behavior byte-
  identical (new code only computes and logs).
- Live run (separate): size 5, `per_window_cap=15`, watchdog equity dd-stop, attended, server-only,
  explicit per-instance "go". Standing rules apply (never from laptop; live only on "go").

## Testing (TDD, mechanics only — edge is the live measurement)

- **`test_topbook_pairquality.py`:** mock-fill harness (pattern of `test_top_book_complete.py`):
  fill Up at 0.40 and Down at 0.55 → merge → assert `merged_cost` accumulates 5×(0.40+0.55) and
  emitted `pair_cost` == 0.95; a second merge at different avgs updates the running VWAP correctly;
  a fully-paired window emits `match_naked=None`, `resid_outcome="flat"`; a naked-residual window
  emits the correct signed `naked_resid` and WON/LOST outcome.
- **`test_pairquality_aggregator.py`:** feed synthetic `topbook_fillquality` lines → assert
  % windows < $1, mean pair_cost, and the outcome distribution are computed correctly.
- Full suite stays green (currently ~458 tests).

## Deliverables

- `merged_cost` accumulator + `completes`/`sells` counters + `topbook_fillquality` event in
  `_top_book_window` (instrumentation only).
- `scripts/_pairquality.py` aggregator.
- Two test files above; full suite green.
- No change to any trading logic. After merge: the bot is ready for a gated live pair-cost
  measurement on explicit "go" (separate step).

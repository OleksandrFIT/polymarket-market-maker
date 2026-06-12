# Auto-Flat (Kill-Naked) Mode — Design

**Date:** 2026-06-12
**Status:** Approved (design), pending implementation
**Author:** poly-quoter

## Problem

The laddered re-quoter accumulates a **naked leg** (one-sided inventory) whenever the
market moves: as price drops, only the cheapening side fills (sellers hit that side's
bids), so one side gets ahead by a full rung. In the 2026-06-12 live ladder test the
naked leg **lost in 6 of 6 windows** — not bad luck but **adverse selection**: the
ladder mechanically buys the side the price is moving *away from*, i.e. the losing side.
A dip is cheap *because* that side is losing. Net test PnL ≈ **−$10.30**; ~70% of the
loss was the (now-fixed) naked-cap overshoot bug, the rest was the naked riding to
resolution and resolving to $0.

The pairs themselves are profitable (e.g. window D: pairs +$0.70). The naked is the
drain. The user's goal: **keep the pairs, kill the naked.**

## Honest ceiling (non-negotiable framing)

Flattening does **not** make the naked profitable. The naked is bought slightly
underwater (adverse selection at entry); that loss is partly baked in the moment it
fills. Flattening at the current bid is ~**EV-neutral vs holding** (market efficiency) —
it **removes variance** (the ±$2.5 swings) and **stops the bleed early** instead of
riding to $0. The win: with a small, predictable flatten cost (~spread + the already-
incurred adverse move), the pairs' thin + now **exceeds** the naked drag, so windows go
net positive. Recomputed window D with fast flatten: pairs +$0.70 − flatten ~$0.15 =
**+$0.55** (vs actual −$1.55). This must be stated plainly; never sold as a profit trick.

## Behavior

Each re-quote tick the live ladder loop checks `naked = inv_yes − inv_no`.

A naked leg is flattened only once it has **persisted ≥ `flatten_grace_sec` (default 20s)**
AND `|naked| ≥ naked_cap`. The grace period is the key design choice:

- **Choppy window:** a temporary imbalance pairs up within the grace window (the other
  side fills) → naked falls below threshold → **no flatten**, no wasted spread, pairs kept.
  This protects the profitable choppy windows (the same windows the trend-detector
  time-gate was added to protect).
- **Trending window:** the other side never fills, the naked persists past the grace →
  it is a real trend, no pair is coming → **flatten** the naked excess and stop feeding
  that side.

When the grace+threshold condition fires, the bot:
1. **Sells** the naked excess of the heavy side at market — a marketable limit SELL at the
   current best bid (`post_only=False`), sized to `|naked|` only.
2. Marks that side **suppressed for the rest of the window** → `plan_ladder` posts no more
   bids there (prevents the "sell → rebuy → sell" churn, and stops loading the loser).

Pairs (the matched `min(inv_yes, inv_no)`) are **never touched** — they ride to resolution
and pay $1 each.

### Persistence tracking

Track the timestamp at which `|naked|` first reached `>= naked_cap` on a given side
(`_naked_since`). Reset it whenever `|naked|` drops below `naked_cap` (the imbalance
paired up — a choppy reversion). Flatten fires when `now - _naked_since >= flatten_grace_sec`.

## Components

| Unit | File | Responsibility |
|---|---|---|
| `plan_flatten(inv_yes, inv_no, naked_cap) -> FlattenDecision \| None` | new `quoter/runner/flatten_planner.py` | Pure: given inventory + cap, decide which side and how many shares to sell (or None). No timing, no I/O. |
| persistence + grace gate | `merge_runner._ladder_window` | Track `_naked_since`; call `plan_flatten` only after grace elapsed; reset on revert. |
| `plan_ladder(..., suppressed: set[str] = frozenset())` | `quoter/runner/ladder_planner.py` | Sides in `suppressed` post nothing (same mechanism as `trend_bias`). |
| `LocalInventory.debit_fill(side, qty, price)` | `quoter/runner/local_inventory.py` | New: subtract sold shares + reduce cost basis proportionally (currently credit-only). |
| flatten execution | `merge_runner._ladder_window` | Place marketable SELL via existing `clob.place_limit(side="SELL", post_only=False, price=bid)`; on success `debit_fill` and add side to window-local `flattened` set. |
| config knobs | `quoter/config.py` | `auto_flat: bool = False`; `flatten_grace_sec: float = 20.0`. Flatten threshold = `naked_cap` (reused, no extra knob). |

`plan_flatten` returns the heavy side and `qty = |naked|` when `|naked| >= naked_cap`,
else `None`. The pure function is threshold-only; the grace timing lives in the live loop
(it owns the clock).

## Data flow (live loop, per tick)

1. Read books, credit fills to `LocalInventory` (existing).
2. Compute `inv_yes, inv_no, naked`.
3. **Persistence:** let `h` = the current heavy side (`YES` if `naked > 0`, `NO` if
   `< 0`). If `|naked| >= naked_cap` and `h` equals the side already being tracked, keep
   `_naked_since`; if `h` flipped or `|naked| < naked_cap`, clear `_naked_since` (and set
   it fresh to `now` if still `>= naked_cap` on the new side). Suppression, once set, is
   **never** cleared mid-window (sticky — see Edge cases); only `_naked_since` resets.
4. **Flatten gate:** if `auto_flat` and `_naked_since` set and
   `now - _naked_since >= flatten_grace_sec`: call `plan_flatten`. If it returns a
   decision, place the marketable SELL. On fill (`order_id` returned), `debit_fill` the
   sold shares and add the side to `flattened`.
5. `plan_ladder(..., suppressed=flattened)` → cancels + posts as today (suppressed sides
   post nothing).
6. Sleep `REQUOTE_SEC`.

## Safety

- Sell **only the naked excess** (`qty = |naked|`), never the paired shares → pairs intact.
- `qty` never exceeds held inventory of that side.
- Marketable SELL is priced at the **real best bid** (not a 0.01 dump). If the bid is
  absent/zero, **skip** the flatten this tick (retry next tick) — never sell into a void.
- If `place_limit` returns no `order_id` (no liquidity / reject), log and retry next tick;
  the naked stays bounded by `naked_cap` (the deployed fix) meanwhile.
- Selling **only reduces** exposure — the path cannot increase risk.
- `auto_flat` defaults **False**; legacy behavior unchanged unless `run_control` enables it.

## Edge cases

- **Suppression is sticky for the window.** Once a side is flattened+suppressed it stays
  suppressed until the window ends, even if `naked` later reverts. Rationale: a side that
  ran away once in a trend will likely run again; un-suppressing re-invites the churn and
  re-loads the loser. (Revisit if live data shows over-suppression.)
- **Both sides:** only the heavy side is ever flattened; the light side keeps trading
  normally (it may still form pairs against existing inventory… but the heavy side is
  suppressed, so new pairs on the flattened side won't form — acceptable; the window has
  declared a trend).
- **Partial sell fill:** if the marketable SELL only partially fills, `debit_fill` the
  filled amount (from the order response); the residual retries next tick.
- **Grace longer than remaining window:** if `< flatten_grace_sec` remains when the naked
  appears, it will still flatten at `END_BUFFER_SEC` via the existing end-of-window
  `cancel_all` — but naked shares are *positions*, not orders, so `cancel_all` does NOT
  sell them. **Therefore:** also fire a final flatten attempt in the loop's last tick
  before `END_BUFFER_SEC` regardless of grace, so a late naked is not stranded to
  resolution. (Grace is a *minimum* wait, but the end-of-window is a hard backstop.)

## Testing

- `plan_flatten`: returns None below cap; returns (heavy side, |naked|) at/above cap;
  picks the correct side for both YES-heavy and NO-heavy; qty never exceeds held.
- `plan_ladder` suppression: a side in `suppressed` yields no posts (and existing rungs
  there are cancelled), other side unaffected.
- `LocalInventory.debit_fill`: reduces inv and cost basis proportionally; never goes
  negative; reduces naked.
- Ladder sim (`test_ladder_sim`): a sustained one-sided dump drives naked to cap; after
  grace the sim flattens (debit) + suppresses; naked returns toward 0 and the suppressed
  side stays out for the window. Non-vacuous: assert a flatten actually occurred and
  pairs survived.

## Operational

- Live trading is **operator-gated**: never launched without the user's explicit "go";
  laptop runs code + unit tests only; live runs only from AWS ca-central-1. Work on `master`.
- `run_control.py` will set `auto_flat=True`, `flatten_grace_sec=20.0` for the next gated
  live test. Deploy = rsync + restart `poly-control` (STOPPED), no auto-launch.

## Out of scope (YAGNI)

- Binance-signal-triggered early sell (the detector can't catch the small moves that cause
  most naked; deferred).
- Taker-complete (buy the missing leg) instead of sell — ~EV-equivalent to flatten but
  locks more capital; flatten frees capital, better for the small bankroll.
- A separate flatten threshold distinct from `naked_cap` (reuse cap; add later only if needed).

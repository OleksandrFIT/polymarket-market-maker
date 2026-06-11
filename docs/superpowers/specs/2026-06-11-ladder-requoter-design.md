# Laddered Re-Quoter — Design

**Date:** 2026-06-11
**Status:** approved (brainstorm), pending implementation plan

## Goal

Capture the cheap-pair (merge) edge the competitor analysis proved is real: instead of
joining top-of-book with one bid per side (fills at fair price → 1–8¢ edge), rest a
**deep static ladder** of bids on both sides so that when one side dips, low rungs fill
cheap (→ ~7¢/pair like the competitor's $0.93 cheap pairs). Stay disciplined: hard
naked-cap, never accumulate directional size (the competitor's losing −$2,226 part).

## Background / evidence

- 114-window competitor analysis (`scripts/analyze_competitor.py`, cheapest-first lens):
  isolated merge edge **+$2,826**, cheap-pair avg **$0.931**, ~7¢/pair; his directional
  adds LOSE (−$2,226). A disciplined pure-merge ladder would out-earn him.
- Our current re-quoter (`plan_requote`) posts ONE bid per side at top-of-book → fair
  price → thin edge. The over-buy fix (`LocalInventory`) and cost-basis gate (`pair_ok`)
  already work and are reused here.
- Cheapest-first is an OPTIMISTIC bound; whether OUR ladder actually fills cheap dips is
  what the first small live run measures.

## Key decisions (from brainstorm)

1. **Ladder anchor is a config TOGGLE** (`ladder_anchor`), so we don't have to guess
   static-vs-chase — the same rung-laying code runs both and we A/B them live:
   - `"entry"` (default, start here) — rungs anchored at window-entry mid, left static, so a
     price dip into a low rung fills it cheap (catches dips; drift covered by the naked-cap).
   - `"book"` — rungs anchored to the CURRENT best bid each tick (a chasing ladder: steady
     fills near fair, catches only fast dips). This is the comparison baseline.
   The first small live run measures whether `"entry"` drops our avg pair cost below the
   ~$0.95 a top-of-book join gives; if static is poorly anchored live, flip to `"book"`.
2. **Naked handling — cap + pull rungs:** when `|naked| ≥ naked_cap`, cancel ALL rungs on
   the heavier side (stop buying the crashing side); keep the opposite side's rungs to
   complete pairs; hold the cheap naked leg to resolution. No taker-rebalance in v1 (YAGNI).
3. **First live size (small):** 5 rungs/side × 5 shares, 3¢ spacing (top to −12¢),
   naked_cap 10 (~$4.50 real risk), per_window_cap 25 (fits the ~$21.50 ladder notional).
   Size is a live-editable config knob; scale only after the
   small run shows real cheap-fill capture.

## Architecture

```
quoter/runner/ladder_planner.py   NEW, pure (no I/O): plan_ladder(...) -> LadderPlan
quoter/runner/merge_runner.py     _requote_window: resting[side] becomes a LIST of rungs
quoter/config.py                  + rungs, rung_size, rung_spacing, naked_cap, per_window_cap
quoter/runner/local_inventory.py  reused unchanged (fill accounting)
tests/test_ladder_planner.py      NEW: placement / cap / pair_ok + lag-fill sim
```

Reuses validated pieces: `LocalInventory`, ask-clamp (`_desired_price`), cost-basis gate
(`pair_ok`). The existing `plan_requote` stays untouched (comparison / fallback).

## Core: `plan_ladder` (pure, deterministic)

Signature:
```
plan_ladder(*, yes_bid, no_bid, yes_ask, no_ask, entry_mid,
            inv_yes, inv_no, yes_cost, no_cost, committed,
            resting, cfg) -> LadderPlan(cancels: list[str], posts: list[Quote])
```
`resting` is `{"YES": list[RestingOrder], "NO": list[RestingOrder]}`.

Per tick:
1. **Capital gate:** if `committed >= cfg.per_window_cap` → cancel all resting, return.
2. **Rung prices** from the anchor selected by `cfg.ladder_anchor` (δ = `cfg.merge_edge` / 2):
   - anchor: `"entry"` → `anchor_yes = entry_mid`, `anchor_no = 1 − entry_mid` (static);
     `"book"` → `anchor_yes = yes_bid`, `anchor_no = no_bid` (chase current bid).
   - YES top = `anchor_yes − δ`; rungs = `top, top − spacing, …` × `cfg.rungs`.
   - NO  top = `anchor_no − δ`; same.
   - Each rung clamped to `0.01 ≤ p` and `p ≤ ask − 0.01` (never cross). Rungs that clamp
     to ≤ 0 are dropped.
   The rung-laying logic is identical for both modes — only the anchor price differs, so the
   two modes are a clean A/B with one config flip.
3. **Naked-cap:** `naked = inv_yes − inv_no`. If `naked ≥ cfg.naked_cap` → desired YES rungs
   = [] (cancel all YES rungs). If `-naked ≥ cfg.naked_cap` → desired NO rungs = [].
4. **pair_ok per rung** (cost-basis): a rung is desired only if the pair it would form costs
   `< $1` using the price already PAID on the held opposite leg when completing
   (`yes_avg`/`no_avg` from cost/inv), else the current opposite rung price. Same rule as the
   over-buy fix, applied per rung.
5. **Per-side inventory target:** a side stops being desired once its filled `inv` reaches
   `cfg.rungs × cfg.rung_size` (e.g. 25 at 5×5) — that side's desired rungs become []. This
   bounds total accumulation per side independently of the naked-cap.
6. **Diff** desired rungs (by price) vs `resting[side]`: a resting order at a desired price is
   kept; a resting order not in the desired set → cancel; a desired price with no resting
   order → post `Quote(side, price, cfg.rung_size)`. A filled rung (gone from `resting`) at a
   still-desired price is re-posted (to catch oscillation around that level) — but only while
   BOTH bounds allow it: the side's `inv` is below the per-side target (step 5) AND the
   naked-cap has not pulled the side (step 3). Whichever binds first stops further posting on
   that side. On the small first config, naked-cap (10) binds before the per-side target (25)
   on a one-sided trend; the per-side target binds when pairs keep forming.

`LadderPlan` = `{cancels: [order_id...], posts: [Quote...]}`.

## Live loop changes (`_requote_window`)

- Capture `entry_mid` once at window entry (the static ladder anchor).
- `resting[side]` is a `list[RestingOrder]` (the rungs).
- Reconcile each rung against `get_open_orders()`; a rung gone without our cancel →
  `LocalInventory.credit_fill(side, size, price)` (existing logic), drop from list.
- `local.reconcile_up` backstop per side (existing).
- Call `plan_ladder` with `local.inv` / `local.cost` / `committed`.
- Apply plan: batch `cancel_orders(plan.cancels)`; post each `plan.posts` rung with the
  forward-cap guard (`committed + post_cost <= per_window_cap`); record placed orders into
  the side's list.
- `finally: cancel_all()` unchanged.
- Branch: `run_forever` uses `_requote_window` (ladder) when `cfg.rungs > 1`, else the old
  single-bid path — so ladder is opt-in by config.

## Config additions (live-editable)

```python
ladder_anchor: str = "entry"  # "entry" (static from entry-mid) | "book" (chase best bid)
rungs: int = 5                # rungs per side
rung_size: int = 5            # shares per rung
rung_spacing: float = 0.03    # price step between rungs
naked_cap: int = 10           # max |naked| → pull heavier side's rungs (the REAL risk bound)
per_window_cap: float = 25    # $ ceiling on capital DEPLOYED (resting + filled) per window
```

**Capital vs risk (important):** `per_window_cap` bounds total capital *deployed* (resting
notional + filled) — it must be ≥ the full ladder notional or the gate cancels the whole
ladder. A 5×5 ladder rests ~$21.50, so `per_window_cap=25`. Actual *risk* per window is the
unhedged naked legs, bounded separately by `naked_cap` (10 shares ≈ $4.50); matched pairs are
hedged (return ~$1 each), not risk. So ~$25 deployed, ~$4.50 at risk, on ~$88 cash.
Existing knobs (`flat_size`, `merge_levels`, `max_naked_shares`) remain for the non-ladder
path. `run_control.py` sets the small first-live config above with the ladder path enabled.

## Error / edge handling

- Rung price ≤ 0 or ≥ ask → dropped (clamp `0.01 ≤ p ≤ ask − 0.01`).
- Extreme `entry_mid` (>0.8 / <0.2): the far side yields fewer valid rungs — acceptable
  (fewer rungs), no special case.
- Partial rung fills → `LocalInventory.reconcile_up` (existing).
- Book fetch timeout/empty → sleep, continue (existing).
- Double safety: naked-cap AND capital-cap; `cancel_all()` in `finally`.

## Testing

- **Pure `plan_ladder`:** rung placement from a given anchor (correct prices/spacing/count);
  `ladder_anchor="entry"` anchors to `entry_mid` while `"book"` anchors to the current bid
  (assert the two modes produce different rung prices when entry_mid ≠ current bid);
  ask-clamp drops crossing rungs; `pair_ok` blocks a rung that would form a ≥$1 pair given a
  held cost basis; naked-cap cancels the heavier side's rungs; capital-cap cancels everything.
- **Lag-fill simulator** (extend `LaggedRequoteSim` style): scripted "Down dumps" window →
  low Down rungs fill cheap, naked stays ≤ `naked_cap`, a pair forms on the cheap fills →
  assert resulting matched-pair avg cost < $1.
- **Invariants:** `|inv_yes − inv_no|` never exceeds `naked_cap` (+ one in-flight rung); no
  matched pair ever costs ≥ $1.

## Out of scope (v1)

- Taker-rebalance / taker-completion of the naked leg (deferred; cap+pull is v1).
- Volatility-adaptive rung spacing (static ladder chosen).
- Live scaling beyond the small first config (only after measured capture).

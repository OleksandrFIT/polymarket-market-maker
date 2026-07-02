# Top-of-Book Live MM Mode (quoter + auto-merge + auto-redeem) — Design

**Date:** 2026-07-02
**Status:** approved (brainstorm), pending implementation plan
**Follows:** `2026-07-01-book-collector-design.md`, `2026-07-01-pair-completion-design.md`,
`scripts/_top_book.py` (the validated strategy sim)

## Goal

Build the live-capable top-of-book market-making mode that replicates the validated
strategy (+1.13% edge / 68% win / 97% matched on 197 real windows; competitor-profile
match): continuously bid best+1tick on BOTH sides of the current BTC 5m window, skew-cap
naked inventory, auto-MERGE matched pairs into $1 (instant capital recycling), auto-REDEEM
resolved residuals — all behind the existing dry-run lock. One attended live test happens
ONLY on the user's explicit go, later.

## Why (measured, 2026-07-01/02)

- Deep-ladder MM is structurally −EV for a small player (adverse selection at every level;
  −59..−14% across queue assumptions). Top-of-book quoting removes it: price improvement
  (tick=0.001) puts us ALONE at our level (queue ahead = 0, no capital needed), and top-of-
  book two-way flow fills BOTH sides symmetrically (loser-fills 49%, matched 97%).
- Fees verified (docs.polymarket.com/trading/fees): makers NEVER pay; takers pay
  0.07·p·(1−p); makers additionally receive 20% rebates daily → our maker edge holds.
- Capital measured: turnover ~$153/window but peak-in-flight WITH instant merge =
  median $13.9 / p90 $17.7 / max $21.9 → our $75-90 bankroll suffices ONLY with auto-merge.
  Without merge peak = full turnover ($153-249) → doesn't fit. Redeem must be automatic at
  288 windows/day or winnings pile up unredeemed and cash drains.

## Hard constraints

- **LIVE stays disabled for the whole build**: `CFG.dry_run=True` + import-time assert, as
  now. The live test is launched ONLY by the user's explicit command, attended.
- The on-chain spike's acceptance test (merge ONE ~$1 pair) is NOT trading (no orders) and
  still requires the user's ok before running.
- Never sell. Buy-side quotes only.

## Components

### 1. Pure planner — `quoter/runner/top_book_planner.py`

`plan_top_book(yes_book, no_book, inv_up, inv_dn, naked_cap, size, tick=0.001) -> list[Quote]`
- Per side: `best_bid = max(bid prices)`, `best_ask = min(ask prices)`; our price =
  `round(best_bid + tick, 3)`.
- Gates (skip that side): empty bids; `our >= best_ask` (never cross); `our >= 0.99`;
  `inv[side] - inv[other] >= naked_cap` (inventory skew).
- Returns target quotes (side, price, size) for both eligible sides. Never sells.

Also a pure diff helper: `diff_quotes(current_resting, target) -> (to_cancel, to_post)` —
cancel resting quotes not in target (wrong price/size), post targets not already resting.

### 2. Runner glue — `merge_runner` branch `strategy == "top_book"`

Loop every REQUOTE_SEC (~1-2s) on the current 5m window:
- Fetch both books → `plan_top_book` → `diff_quotes` vs our resting → `_cancel_orders` +
  `_place_limit(post_only=True)` (both are existing dry-run-locked no-ops).
- Inventory via the existing lag-proof machinery (`LocalInventory` credit-on-vanish +
  reconcile, posted-shares backstop) — reused, not rebuilt.
- Caps: `per_window_cap` = spend ceiling per window (40.0 — bounds capital even if merge
  fails), naked cap via planner gate, FORCE STOP + watchdog inherited.
- Window end: cancel all; residual rides to resolution (redeem sweeper picks it up).

### 3. Config (phase-27)

`strategy="top_book"` | `tb_size=5` | `tb_naked_cap=10` | `tb_tick=0.001` |
`tb_merge_min=5.0` | `per_window_cap=40.0` | `dry_run=True` (locked).
`run_control`: STRATEGY env gains `top_book`; its CFG asserts `dry_run is True`.

### 4. On-chain ops — `quoter/chain/positions_ops.py`

- `merge_pairs(condition_id, qty)` → burn qty Up+Down pairs → receive $qty USDC.
- `redeem(condition_id)` → claim resolved winnings.
Positions live on the PROXY (funder) wallet; CTF `mergePositions`/`redeemPositions` must be
executed as the proxy. **Spike (first plan task) picks the path:**
- Path A (preferred): Polymarket gasless relayer — same mechanism as the UI "Merge" button
  (EOA-owner signature → relayer executes via proxy). Semi-documented; spike reverses it.
- Path B (fallback): direct on-chain via `web3` — EOA executes through the proxy contract
  (`sig_type` determines proxy type); needs a few $ of POL gas in the EOA.
**Spike acceptance:** one real merge of a ~$1 pair on the server wallet, +$1 USDC visible.
Requires user ok (not trading — no orders placed).
Safety: module never touches orders; qty bounded by held inventory; no new token approvals.

### 5. Orchestration

- **Merge:** each tick, `matched = min(inv_up, inv_dn)`; if `matched >= tb_merge_min` →
  `merge_pairs`, debit both sides, credit cash. Failure → retry next tick; quoting never
  blocks on merge (spend cap bounds capital).
- **Redeem sweeper:** independent coroutine every ~60s: `data-api/positions?redeemable=true`
  → `redeem()` each. Isolated from the trading loop (its failure must not stop quoting).
- **Dashboard/state:** add `merged_today`, `redeemed_today`, `matched_pct`, `fills_window`
  to the snapshot — the live-test decision metrics (real theta vs sim) visible in real time.

## Testing

- `top_book_planner` + `diff_quotes`: full TDD (price=best+tick; never-cross; 0.99 gate;
  skew gate; empty book; both sides; cancel/post diff cases).
- Pure `plan_merge(inv_up, inv_dn, merge_min) -> qty|None` + tests.
- On-chain network calls: thin I/O, accepted via the spike test (no unit tests).
- All existing tests stay green; `test_run_control_cfg` gains the `top_book` case asserting
  `dry_run is True`.

## Out of scope (YAGNI)

- No selling, no taker completion (top-of-book matches naturally at 97%).
- No multi-asset / 15m; BTC 5m only.
- No reward-farming optimization (rebates are unmodeled upside).
- No auto-lift of the live lock — human-gated forever.

## Success criteria

1. Full suite green; new pure units TDD-tested.
2. Dry-run: bot on the server quotes top-of-book in `fivemin`-style intent logs (0 real
   orders), dashboard shows the new metrics.
3. Spike: one real ~$1 pair merged (after user ok) — merge path proven.
4. Redeem sweeper redeems a resolved test position.
5. Everything deployed on the server behind the lock, waiting for the user's explicit go
   for the attended live test (size 5, a few windows, measure real theta/matched%/edge).

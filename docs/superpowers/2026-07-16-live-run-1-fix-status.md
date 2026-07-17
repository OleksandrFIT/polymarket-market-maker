# Live Run #1 — Every Problem Seen, and its Fix Status

Enumerates ALL issues surfaced during the 2026-07-16 live run (4 windows / 20 min / −$9.88).
"Fixed" = code + tests landed. "Needs live" = the fix is unit-tested and the root cause is proven,
but end-to-end confirmation requires the exchange (a future, separately-authorized short live).

| # | Problem | Severity | Status | Commit |
|---|---|---|---|---|
| 1 | **`invalid maker amount` ×26 — sell-loser rejected 90%** | 🔴 blocker | **FIXED** (needs live to confirm end-to-end) | `<this>` |
| 2 | `place_exception` logged only error+token, no order params | 🔴 diagnosis | **FIXED** | `110afa8` |
| 3 | Watchdog `grep -c \|\| echo 0` printed two zeros → arithmetic crash | 🔴 safety | **FIXED** | `ff35ffe` |
| 4 | Watchdog exec-bit stripped by `git archive` deploy → `Permission denied` | 🟡 safety | **fix = chmod on deploy** (procedural) | — |
| 5 | Service ran the WRONG tactic (no `REGIME_GATE` → chop_gate=False) | 🔴 config | **FIXED** (drop-in `REGIME_GATE=0`) | server |
| 6 | Watchdog baseline stale/root-owned (Jul 8, 61.04) → would false-stop | 🔴 safety | **FIXED** (deleted → fresh baseline) | server |
| 7 | Cross-window on-chain position "accumulation" | 🟡 → re-assessed | **NOT a bug** (see below) | — |
| 8 | `assumed_shares` fires often (25 = 21% of spend) | 🟢 info | **not a bug** (credits real; telemetry handles it) | — |
| 9 | `crosses book` ×12 (post-only rejected on a moved book) | 🟢 cost | **inherent** to maker discipline; retry next tick | — |
| 10 | Accumulation quotes at a 0.001 tick on a 0.01-tick market | 🟡 latent | **document → align at re-calibration** | — |

---

## #1 — the blocker: FIXED, root cause PROVEN offline

**Diagnosis (offline, no trading):** `scripts/_diag_sell_order.py` builds sell orders locally via
`create_order` (which signs but does NOT post) across a size×price grid. Result: **every order builds
with valid maker/taker amounts down to price 0.001** (the client's local minimum). So the malformation
is server-side. The market tick is **0.01**; the sell-loser sent the **raw book bid**, and near
resolution the loser crashes **sub-tick** (offline E-audit already measured mean loser bid @freeze =
**0.009**). A sub-tick price → server rejects `invalid maker amount`. Live and offline agree exactly.

**Fix (`floor_to_tick`/`ceil_to_tick` in top_book_planner):**
- SELL floors the bid to 0.01. If it floors **below one tick** (bid < 0.01) there is no valid order —
  the leg is worth ~0, nothing to sell into — so it **SKIPs and logs `topbook_sell_skip{sub_tick_bid}`**
  (not a kill, no API call). This ends the invalid-order spam (was retried every tick, 14×/window).
- Completion **ceils** the ask to 0.01 (a taker buy must reach the ask) and gates `buy_px < 0.99`.
- `sell_skips` added to telemetry (skip ≠ kill ≠ recovery).

**Important honesty:** this does NOT recover the money. When the loser is sub-tick it is genuinely
worth ~0 — the loss was taken UPSTREAM (we accumulated a losing leg), exactly the offline audit's
"93% no_bid_on_loser, structurally unfixable post-hoc." The fix makes the branch **correct and
observable**, not profitable. The real lever remains upstream: accumulate less naked.

**Still needs live to confirm:** that the exchange now accepts the tick-valid sells (recovering the
7% of E that had a real ≥0.01 bid) and cleanly skips the rest. Unit-tested; not yet exchange-confirmed.

## #7 — cross-window accumulation: I OVERSTATED it; it is NOT a growing-position bug

Mid-run I wrote "unsold losers accumulate on-chain, both safeguards are per-window blind, exposure
grows linearly." **That was wrong and I'm correcting it.** Each BTC 5m window is a **separate market
with a separate token that resolves independently**. A leg that can't be sold rides to **that window's**
resolution and becomes $0 (loser) or is redeemed (winner) — it does not carry a growing position into
the next window's market. The `Down×15 + Down×5` I saw on-chain were **two different windows'** settled
loser dust (~$1.35, resolving to $0), not one growing position.

So the per-window `naked_cap = 6` is correct (it bounds each separate market), and the watchdog reading
per-window naked is correct (old settled legs are worth ~$0, not live risk). The cumulative −$10 across
windows is exactly what the **dd-stop** exists to bound, and it did. The only real residual is **dust
that needs periodic redemption**; it is not a safety hole. No cap/watchdog change needed.

## #10 — accumulation tick (latent, needs a calibration decision, NOT a blind fix)

`plan_top_book`/the sim quote at `round(best+0.001, 3)` — a 0.001 tick — but the market tick is **0.01**,
so these bids are sub-tick. They were NOT rejected in the run (pairs filled → the exchange rounds/accepts),
so it is not the sell bug. But the real fill price/rate then differs from what the sim assumes, which
biases the pair-cost measurement. **Do not blind-fix:** changing the accumulation tick to 0.01 changes
the strategy (best+0.01 vs best+0.001) AND desyncs from the calibration. It must be decided together with
the offline re-pricing (below), with sim and prod moved in lock-step.

## What the run invalidated (must be re-priced offline before any re-measurement)

The offline model prices **case C (sell the loser) = 64% of windows at 86.1% recovery**. Live, the sell
is only viable when the loser bid is ≥ one tick — which the E-audit says is **~7%** of these (the other
93% crash sub-tick and are now correctly SKIPPED). So most C windows are really **E (ride to $0)**. The
sim's **−$0.74/window naked cost is a large understatement** and the **+$0.50/window anchor** was built
on a sell branch that recovers far less than modelled. Re-run the case audit with:
- the sub-tick skip (sell only when bid ≥ 0.01),
- a realistic sell-recovery (≤ 86.1% only on the ≥0.01 subset),
- the tick decision from #10.

## Bottom line

The blocker is fixed and its root cause is proven offline. The other nine items are fixed, procedural,
inherent, or (one) an honest over-statement now corrected. **No re-measurement should be pre-registered
until the offline model is re-priced with the real sell behaviour** — otherwise the go/no-go thresholds
would again come from a config the live code doesn't match. Live remains LOCKED OFF.

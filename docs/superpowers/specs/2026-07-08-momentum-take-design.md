# Momentum-Take Strategy — Design Spec

**Goal:** Replicate 0xb27b's decoded system as a NEW bot strategy: reactive momentum-take of the
winning side at price extremes + cheap loser + continuous merge floor + a small directional
residual held to resolution. Live-test it (it CANNOT be validated offline).

**Status:** New strategy `momentum`, separate from the validated passive `top_book` (which stays
untouched). First attended live test with a hard −$30 loss stop.

---

## Background — what we decoded (from 0xb27b's real trade tape)

His per-window play-by-play (e.g. `btc-updown-5m-1783521900`) shows: he watches which side is
RISING, TAKER-buys that side (the winner) chasing it up (0.57 → 0.95), TAKER-buys the falling
side (loser) cheap (0.11 → 0.02), MERGES matched pairs continuously (recycles capital, locks the
arb floor), and ends net-long the winner (redeems $1). He trades at EXTREMES where the crypto
taker fee (peaks at 0.50, ~0 at the edges) is minimal. It is directional momentum-following
wrapped in merge-arb — the OPPOSITE of our passive maker (which gets adverse-selected onto the
loser). This is regime-dependent (trends persist → +; whipsaw reversals → the tail loss, his
worst window ≈ −$751), with the merge floor limiting downside.

## Why there is no offline validation

Every offline backtest we built (passive, chase, tilt, ladder, momentum) is NON-decision-grade:
the toxicity model proved the harness cannot even reproduce passive's live behaviour (it shows
passive +0.68% while live is −EV) because it does not model queue/adverse-selection. So the edge
of THIS strategy can only be measured LIVE. Offline/unit tests validate MECHANICS only.

## Non-goals (YAGNI)

- Do NOT modify `top_book`, `five_min`, or the passive maker path.
- No offline edge claim. No auto-tuning. No SELL logic (0xb27b never sells).
- No scaling beyond the small test size until the live edge is known.

## Architecture

New strategy path `momentum` in `quoter/runner/merge_runner.py`, dispatched from `run_forever`
alongside `top_book`/`five_min`, with a new `_momentum_window(m, mid)` tick loop. Config in
`quoter/runner/run_control.py` under `STRATEGY == "momentum"`, live-locked behind `LIVE_GO=1`.
Reuses existing units: `chase_signal` (causal momentum), `_place_limit(..., post_only=False,
order_type="FOK")` (taker), `_merge_pairs`, `_order_matched`, the watchdog, the redeem-sweeper.

### Units

**Unit 1 — momentum signal (reused): `chase_signal(mid_hist, now_ts, lookback, threshold)`**
Already exists + unit-tested. Returns the rising side ("Up"/"Down") or None. Fed the Up-mid
history seen so far each tick.

**Unit 2 — taker fee helper: `taker_fee(price) -> float` (new, pure, unit-tested)**
Per-share crypto taker fee = `FEE_RATE * min(price, 1 - price)` with `FEE_RATE = 0.018` (peaks
at 0.50, ~0 at extremes). Used to bound spend and gate takes (never take if fee makes the leg
un-economic). Pure function in `quoter/runner/top_book_planner.py` or a small new module.

**Unit 3 — `_momentum_window` tick loop (new, in merge_runner)**
Per tick (~cadence sec, until END_BUFFER_SEC before expiry):
1. fetch both books; append Up-mid to `mid_hist`.
2. `sig = chase_signal(...)`. If None → no trade this tick (chop is sat out).
3. If `sig`:
   - MOVER = sig, FADER = other side.
   - TAKE mover at its best ask (FOK) if `ask <= chase_max` (0.95): size `tb_size`, bounded by
     `residual_cap` (mover inv − fader inv < residual_cap) and per-window budget.
   - TAKE fader at its best ask (FOK): size `tb_size`, to pair.
   - Credit only real `_order_matched` fills (killed FOK → 0). Spend includes `taker_fee`.
4. MERGE `min(inv_up, inv_dn)` pairs each tick (recycle + floor).
5. NEVER sell. The net residual (net-long the mover) is capped at `residual_cap` and rides to
   resolution (winner redeems $1, loser expires).
- Committed-spend gate: total taker spend ≤ `per_window_cap`. Skew/residual gate: mover inv −
  fader inv ≤ `residual_cap`.
- `finally: cancel_all()` backstop (FOK leaves nothing resting, but keep the invariant).

## Parameters (small test values; env/config)

| param | value | meaning |
|---|---|---|
| `tb_size` | 5 | shares per take |
| `residual_cap` | 5 | max net directional exposure / window |
| `per_window_cap` | 15 | max taker $ spend / window |
| momentum `lookback` | 30 s | react to early moves (like him) |
| momentum `threshold` | 0.03 | mid move to trigger |
| `chase_max` | 0.95 | never take the mover above this |

## Safety

- Live ONLY via `STRATEGY=momentum LIVE_GO=1`; default STOPPED + `dry_run=True` (hard-locked
  assert, same pattern as top_book). Other strategies stay locked even with LIVE_GO.
- Watchdog: equity **dd stop −$30** (this run), naked/residual tripwire (residual_cap + margin).
- Attended; server-only; small size.
- Directional tail: worst single-window loss ≈ `residual_cap × ~$0.5 + fees` ≈ −$3 (residual
  goes to $0 on a wrong-way window); watchdog force-stops at cumulative −$30.

## Testing

Offline/unit tests cover MECHANICS ONLY (edge is unmeasurable offline):
- `taker_fee`: peaks at 0.50, ~0 at extremes, symmetric.
- `_momentum_window` (mock harness like `test_top_book_complete.py`): on a rising-Up tape it
  TAKES Up (mover) + Down (fader), merges pairs, caps residual at `residual_cap`, never SELLS,
  credits only real FOK fills, bounds spend at `per_window_cap`.
- `run_control` cfg guard: `momentum` is dry-run without LIVE_GO; caps as specified.
- Full suite stays green.

## Success criteria (the live test answers ONE question)

Is the momentum-take strategy net-positive for us live, or does adverse selection / whipsaw make
it −EV like passive? Judged on: realized PnL over the run (until −$30 stop or an attended call),
per-window pair economics (mover avg px, fader avg px, residual outcome), and whether the mover
we chase actually resolves as the winner more often than not (the momentum edge). This is a
MEASUREMENT, not an expected profit — high variance, regime-dependent, capped at −$30.

## Deliverables

- `taker_fee` + tests.
- `_momentum_window` + `momentum` dispatch + run_control config + tests.
- No change to `top_book`/passive path.
- After merge: arm + attended live test (separate, gated, on explicit "go").

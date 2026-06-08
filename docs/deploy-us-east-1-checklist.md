# us-east-1 Deployment Checklist (preparation — NO live trading)

**Goal:** move poly-quoter into AWS us-east-1 to cut RTT ~110 ms → single-digit ms,
so both merge legs fill and the measured 1–2¢/pair edge becomes capturable. This
checklist is **preparation + measurement only** — it does NOT enable live trading.
Flipping to real money is a separate, explicit decision (see §7).

Secrets note: reference `.env` keys by NAME only (POLY_PRIVATE_KEY, POLY_API_KEY,
POLY_API_SECRET, POLY_PASSPHRASE). Never paste their values anywhere.

---

## 1. Infrastructure
- [ ] EC2 instance in **us-east-1** (N. Virginia). Start small: `c7i.large` /
      `c6i.large` (compute-optimized, steady clock). Upgrade only if CPU-bound.
- [ ] Pick the **availability zone** that minimizes latency to the CLOB endpoint
      (measure in §2 from 2–3 AZs, keep the best).
- [ ] Ubuntu 22.04/24.04 LTS, Python 3.11 (match local), `.venv` with same deps
      (`pip install -e .` / requirements lock).
- [ ] Time sync: `chrony` enabled (accurate clock matters for window timing + logs).
- [ ] Outbound HTTPS/WSS open to `clob.polymarket.com`,
      `ws-subscriptions-clob.polymarket.com`, `data-api.polymarket.com`,
      `gamma-api.polymarket.com`, `stream.binance.com` (Binance only if ever needed;
      merge-maker does not use it).

## 2. Measure REAL latency to the CLOB (the whole point)
- [ ] HTTP RTT: `for i in $(seq 20); do curl -o /dev/null -s -w "%{time_connect} %{time_total}\n" https://clob.polymarket.com/time; done` — record median connect + total.
- [ ] WS round-trip: connect to `wss://ws-subscriptions-clob.polymarket.com/ws/market`,
      timestamp a subscribe→first-book-update cycle; record median.
- [ ] DNS/route: `mtr -rwzbc100 clob.polymarket.com` — confirm few hops, no detour
      out of us-east-1.
- [ ] **Target:** book-update RTT ≤ ~10 ms. If it's still tens of ms, try another AZ
      / instance family before going further.
- [ ] Record the numbers — they replace the `paper_latency_ms=110` assumption baked
      into the paper fill model.

## 3. Verify queue + fee economics (the edge is thin — 1–2¢)
- [ ] Confirm Polymarket **maker** economics: post-only orders, and the current
      **maker fee** (must be ~0 or rebate — any taker fee kills a 1¢ edge).
- [ ] Confirm the on-chain **merge** path cost: `mergePositions` is an L2 (Polygon) tx
      — gas per merge must be « the locked spread per batch. Batch merges to amortize.
- [ ] Sanity: re-run `scripts/_live_merge_feasibility.py` FROM the server — confirm
      `bid_Up + bid_Down < $1` still holds and measure achievable depth at our size.

## 4. App tuning for low latency (config only, still paper)
- [ ] Re-run paper ON the server first. Lower `requote_min_interval_ms` (currently 50)
      toward the new RTT; profile the async loop so our own processing isn't the
      bottleneck.
- [ ] Update the paper fill model's `paper_latency_ms` to the **measured** value (§2)
      and `paper_queue_position` to a realistic seat — so paper reflects the new
      reality, not Slovakia.
- [ ] Re-measure paper behavior: naked exposure should now stay near zero and **both**
      legs should start filling (the model will show merges firing on real pairs).
- [ ] Only after that looks right, consider loosening `max_naked_shares` toward the
      competitor's tolerance (low latency makes naked benign).

## 5. Reliability (must run 24/7)
- [ ] Run under `systemd` (or supervisor) with auto-restart; capture stdout to logs.
- [ ] Verify WS auto-reconnect + `ws_stale_timeout_sec` behavior under a forced drop.
- [ ] Dashboard reachable (SSH tunnel or locked-down security group — do NOT expose
      the dashboard publicly).
- [ ] Confirm `RiskGuard` halts on `max_daily_loss_usd`; set it conservatively.
- [ ] Log rotation; disk for `state.db` growth; a daily `state.db` backup.

## 6. Paper validation in us-east-1 (go/no-go BEFORE any live talk)
Run paper on the server for a meaningful window and confirm:
- [ ] Both legs fill (not just the falling one) → merges fire on real Up+Down pairs.
- [ ] Realized merge spread per pair ≈ 1–2¢ (matches the book measurement).
- [ ] Naked exposure stays bounded and small.
- [ ] No crashes / no stuck quotes / no runaway risk.
If any of these fail, the latency/queue assumption is wrong — fix before §7.

## 7. The live-mode gap (NOT built — explicit decision required)
Currently the bot is **paper only by design**. Before real money, these must be built
and carefully reviewed (each is a deliberate, separately-approved step):
- [ ] Live execution path (real order placement/cancel via authenticated CLOB) —
      currently a `LiveExecutor` stub; needs end-to-end testing.
- [ ] On-chain **CTF `mergePositions`** tx (Polygon) wired into the merge step.
- [ ] Funded proxy wallet + the four `.env` API credentials (by name only).
- [ ] **Start tiny:** smallest sizes, low `per_market_cap_usd`, tight
      `max_daily_loss_usd`; prove positive realized PnL on small live size before
      scaling. Treat the first live capital as tuition.

## Go / no-go summary
us-east-1 fixes the dominant problem (latency → toxic naked leg). It is **necessary,
not sufficient**: you still need favorable queue/fees (§3), a clean paper validation
on the box (§6), and a carefully-built, small-size live rollout (§7). Do not scale
until §6 passes and §7 has shown positive realized PnL in small live size.

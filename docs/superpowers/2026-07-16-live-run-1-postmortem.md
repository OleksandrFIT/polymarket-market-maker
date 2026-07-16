# Live Run #1 — Post-Mortem (2026-07-16)

**Verdict: INSUFFICIENT n — the measurement is INVALID. Not GO, not NO-GO.**
The pre-registered metric (`effective_pair_cost` on causal-chop windows) was **never read** and will not be:
the run executed with a **broken sell-loser branch**, so the windows do not test the hypothesis.

**Cost: −$9.88** (equity 52.87 → 42.99). Inside the pre-registered dd-stop (−$10); the watchdog had
one breach (x1) and would have stopped it seconds later on its own. Capital protection worked.

---

## 1. What ran

| | |
|---|---|
| Duration | **21:20:02 → 21:39:49 UTC ≈ 20 minutes** |
| Windows completed | **4** (of the pre-registered n ≥ 80) |
| Config | `LIVE_GO=1 STRATEGY=top_book REGIME_GATE=0` — clock-only + observer, cap 6, size 5, no early phase, near-end-only completion, link_margin 0.01, freeze 45 |
| Config = pre-registered | **YES** (verified against the frozen sim config before START) |
| Server | AWS ca-central-1 `<SERVER_IP>`, HEAD `d18c3a8`+ |
| Watchdog | armed BEFORE start, fresh baseline 52.87, `LIMIT=-10 DD_NEEDED=2 NAKED_LIMIT=8` |

## 2. Per-window

| W | close | hindsight | observer<br>would_revoke | merged | resid | sell<br>killed/tried | cap | shift | assumed | spent | rebate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | clock@256s | trend | 200s | 15 | flat | 0/1 | 1 | 2 | **10** | 14.91 | .155 |
| 2 | clock@257s | reversal | 236s | 10 | **LOST** | **12/13** | 0 | 0 | 0 | 12.60 | .158 |
| 3 | clock@256s | chop | 224s | 15 | WON | **14/14** | 0 | 1 | 0 | 14.76 | .180 |
| 4 | clock@257s | chop | 212s | 5 | flat | 0/1 | 1 | 1 | **15** | 14.96 | .183 |

**Totals:** 45 pairs merged · spent $57.23 · rebate $0.676 · sell **29 tried / 26 killed (90%)** ·
complete **0 tried** · assumed **25 shares ($11.95 = 21% of spend)** · cap_override 2 · shift 4 ·
resid {flat 2, LOST 1, WON 1} · hindsight {chop 2, reversal 1, trend 1}

---

## 3. THE FINDING — the sell-loser branch is broken live

```
'invalid maker amount'  ×26   ← 400 from the API, NOT a thin-book FOK kill
sell: 29 attempts, 26 killed = 90%
W2: 12/13 killed   W3: 14/14 killed  (the bot retried every tick and failed every time)
```

**Mechanism of loss:** the bot accumulates a leg → it becomes the loser → near-end tries to sell it →
**the API rejects the order as malformed** → the leg is never sold → it rides to resolution → full loss,
with the protective branch dead. Proof in the positions: `Down ×15 @0.09` (bought ~0.5) and
`Down ×5 @0.365` were sitting on-chain, unsellable.

**Root cause NOT diagnosed.** Hypothesis: a size/precision violation when selling at a very low price
(5 shares × $0.01 = $0.05 notional — likely under a minimum). Both sells that DID succeed were the two
windows where the branch fired only once (W1, W4); the windows where it retried (W2, W3) failed 100%.
**This must be diagnosed before any new measurement.**

**Why no amount of offline work could have caught it:** the branch is only reachable with real fills.
No fills → no naked leg → `sell` is never called. 519 unit tests, a 2 500-window sim and two dry-runs
all left this code path unexecuted. **20 minutes of live money found it.**

## 4. Why this invalidates the offline economics (not just this run)

The offline model says **case C (sell the loser) = 64% of windows** and prices it at
**86.1% recovery** of the leg's freeze-time value. Live, the sell is **rejected 90% of the time**.
Therefore:

- Those C windows do not become "sold at 86% recovery" — they become **E (rides to resolution)**.
- The sim's naked cost of **−$0.74/window is a large UNDERSTATEMENT**.
- The headline **+$0.50/window (shadow)** was computed on a branch that does not work in production.

This is a bigger correction than the queue/adverse-selection cost the run was meant to measure.

## 5. Secondary findings

- **`assumed_shares` = 25 (21% of spend).** The `_order_matched` lookup failed 5 times; each time the
  code assumed a full 5-share fill. **These credits were REAL** — proven by 45 successful merges with
  `merge_fails=0` (a phantom share cannot be merged on-chain). So no phantom materialised, but the
  fallback fires far more often than "a handful", and the metric would have needed the with/without
  correction the `assumed_notional` telemetry was built for.
  *Reporting error to correct: mid-run I called this "a one-off burst" off two data points two seconds
  apart. It was not — it kept firing. The call was more confident than the data.*
- **`invalid post-only order: order crosses book` ×12** — known since July; costs fill-rate (i.e. the
  very quantity under measurement), does not corrupt accounting.
- **Completion (case B) fired 0 times in 4 windows.** Consistent with the offline structural finding:
  the missing leg IS the winner, so it is expensive by the same reason it did not fill.
- **Observer fired 4/4 (100%)** at 200–236s, on hindsight {chop 2, reversal 1, trend 1} → it would have
  revoked **both chop windows** = 50% false-positive. Consistent with the 33–56% measured offline, and
  further confirmation that acting on it (trend-revoke) would have been wrong.

## 6. What DID work (verified on real money)

- **Merge**: 45 pairs, `merge_fails=0`.
- **Linked-pair cap**: observed pairs 0.61+0.38 = 0.99, 0.64+0.35 = 0.99 — **no pair ≥ $1**.
- **cap_override ×2** — the branch a dry-run structurally cannot reach, correct on real fills.
- **Clock-only close**: 4/4 at 256–257s, `detector=chop` 4/4 — never acted on trend, as designed.
- **naked ≤ 6** at all times; **spend ≤ $15/window** (max 14.96).
- **Telemetry**: all 23 fields written every window, including the new `sell_*` / `assumed_*`.
- **Watchdog**: fresh baseline, tracked correctly, x1 breach at −$10.28 → would have stopped on its own.

## 7. Money

```
baseline equity 52.87  →  42.99      dd = −$9.88
still on-chain: ~$5 of Down legs (unsellable, will resolve on their own)
spent 57.23 / merged 45 pairs / rebate 0.68
```

## 8. Lessons

1. **The live branch coverage gap is real and expensive.** `sell-loser`, `completion` and `cap_override`
   are only reachable with fills. Two of the three had never executed before this run. The one that
   mattered most was broken.
2. **A sim that always executes an action cannot price that action.** The sim's `sell` fires whenever a
   bid exists — it structurally cannot model rejection. Any branch the sim "always wins" is unpriced.
3. **Confidence must track the data.** My "one-off burst" call on `assumed` was made from two points and
   was wrong; the honest statement was "2 so far, watching".
4. **The pre-registration worked.** Operational failure → stop → "INSUFFICIENT n", metric untouched. No
   temptation to read a number built on broken code. The dd-stop bounded the cost to ~$10 as designed.

## 9. Open, before any re-run

1. **Diagnose `invalid maker amount`** — reproduce a sell at $0.01–0.09 against the API; check minimum
   notional / size precision / tick rounding on the SELL path. This is the blocker.
2. **Then re-price the offline model**: with a realistic sell-rejection rate, re-run the case audit —
   C→E migration will move `naked cost` and the +$0.50/window anchor materially.
3. Only then consider a second measurement, with fresh pre-registration.

**State: live is LOCKED OFF.** `live.conf` removed from systemd, process restarted without `LIVE_GO`
(`dry_run=True`), `mode=STOPPED`, **0 open orders on the exchange** (verified via CLOB).

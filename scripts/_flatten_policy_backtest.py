"""Compare naked-handling POLICIES on many historical BTC 5m windows.

Goal: the user wants "stable profit, no drawdowns". The naked is the variance.
This backtest does NOT just report mean PnL — it reports the DISTRIBUTION
(worst window, #windows < -$2, std) so we can see if ANY policy removes the
deep-loss windows while keeping the thin pair edge.

Policies on a window that ends up naked:
  ride        : hold the naked leg to resolution (current behaviour, auto_flat off)
  gate@t      : at minute t, if the pair can still complete <$1 -> taker-buy the
                light leg (COMPLETE); else taker-SELL the naked leg (cap loss)
  complete@t  : at minute t, always close the naked — COMPLETE if <$1 else SELL
                (same as gate@t here; kept for clarity)

Honest model limits: prices via Binance klines + a Phi win-prob model; the
taker ask/bid spread is MODELLED (half_spread), not observed. Treat as
DIRECTIONAL evidence, not exact dollars. Read-only.
"""
import urllib.request, json, statistics
from quoter.config import Config
from quoter.runner.trend_detector import win_prob_up, sigma_remaining, detect_bias

cfg = Config(trend_confidence=0.40, trend_gate_sec=600.0, trend_vol_fallback=30.0,
             rungs=2, rung_size=5, rung_spacing=0.03, merge_edge=0.02,
             naked_cap=5, min_buy_price=0.42)
HALF_SPREAD = 0.015          # modelled taker half-spread (Polymarket BTC 5m ~1-2c)
RUNG = cfg.rung_size
UA = {"User-Agent": "Mozilla/5.0"}

kl = []
end = ""
N_BATCHES = 16
for b in range(N_BATCHES):
    u = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000"
    if end:
        u += "&endTime=" + str(end)
    try:
        part = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25))
    except Exception as e:
        print("batch", b, "err", e); break
    if not part:
        break
    kl = part + kl
    end = part[0][0] - 60000
kl = sorted(kl, key=lambda k: k[0])
windows = []
i = 0
while i + 5 <= len(kl):
    if (kl[i][0] // 1000) % 300 != 0:
        i += 1
        continue
    windows.append(kl[i:i + 5])
    i += 5
rungs = [round(0.49 - r * cfg.rung_spacing, 2) for r in range(cfg.rungs)
         if round(0.49 - r * cfg.rung_spacing, 2) >= cfg.min_buy_price]


def simulate(w):
    """Return per-minute state: up_cost/dn_cost (maker fills) and the price path.
    up_filled/dn_filled are lists of (minute, price)."""
    strike = float(w[0][1]); final = float(w[4][4])
    up_filled = []; dn_filled = []; buf = []
    pmid = []   # (minute, p_up_close, sigma) for policy pricing
    for t in range(5):
        ts = w[t][0] // 1000
        hi = float(w[t][2]); lo = float(w[t][3]); cl = float(w[t][4])
        buf.append((cl, ts))
        tl = (5 - (t + 1)) * 60
        sig = sigma_remaining(buf, max(tl, 1), cfg)
        pmid.append((t, win_prob_up(cl, strike, sig), sig))
        if tl <= 0:
            continue
        up_lo = win_prob_up(lo, strike, sig)        # Up cheapest at BTC low
        dn_lo = 1.0 - win_prob_up(hi, strike, sig)  # Down cheapest at BTC high
        bias = detect_bias(cl, strike, sig, tl, cfg)
        # live binds naked at post_cap = naked_cap + rung (a rung posted at
        # naked==cap overshoots by one rung -> max naked 10, as seen w7/w10)
        cap = cfg.naked_cap + RUNG
        if bias != "DOWN":
            for r in rungs:
                naked_if = (len(up_filled) * RUNG + RUNG - len(dn_filled) * RUNG)
                if r not in [p for _, p in up_filled] and up_lo <= r and naked_if <= cap:
                    up_filled.append((t, r))
        if bias != "UP":
            for r in rungs:
                naked_if = (len(dn_filled) * RUNG + RUNG - len(up_filled) * RUNG)
                if r not in [p for _, p in dn_filled] and dn_lo <= r and naked_if <= cap:
                    dn_filled.append((t, r))
    return strike, final, up_filled, dn_filled, pmid


def pnl_window(w, policy, gate_t=4):
    strike, final, up_f, dn_f, pmid = simulate(w)
    win = "Up" if final >= strike else "Down"
    up_sh = len(up_f) * RUNG; dn_sh = len(dn_f) * RUNG
    cost = sum(RUNG * p for _, p in up_f) + sum(RUNG * p for _, p in dn_f)
    extra_cost = 0.0; proceeds = 0.0
    # apply policy if naked at the gate
    if policy != "ride" and up_sh != dn_sh:
        # price at gate minute
        _, p_up, _ = pmid[gate_t]
        up_ask = min(0.99, p_up + HALF_SPREAD); up_bid = max(0.01, p_up - HALF_SPREAD)
        dn_ask = min(0.99, (1 - p_up) + HALF_SPREAD); dn_bid = max(0.01, (1 - p_up) - HALF_SPREAD)
        if up_sh > dn_sh:                       # naked Up
            naked = up_sh - dn_sh
            held_avg = sum(RUNG * p for _, p in up_f) / up_sh
            if held_avg + dn_ask < 1.0:         # completable -> taker-buy Down
                extra_cost += naked * dn_ask; dn_sh += naked
            else:                               # uncompletable -> sell naked Up
                proceeds += naked * up_bid; up_sh -= naked
        else:                                   # naked Down
            naked = dn_sh - up_sh
            held_avg = sum(RUNG * p for _, p in dn_f) / dn_sh
            if held_avg + up_ask < 1.0:
                extra_cost += naked * up_ask; up_sh += naked
            else:
                proceeds += naked * dn_bid; dn_sh -= naked
    payout = (up_sh if win == "Up" else dn_sh) * 1.0
    return payout + proceeds - cost - extra_cost


def stats(pnls):
    n = max(1, len(pnls)); tot = sum(pnls)
    worst = min(pnls) if pnls else 0.0
    big = sum(1 for p in pnls if p < -2.0)
    sd = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
    return tot, tot / n, worst, big, sd, n


def report(name, pnls):
    tot, mean, worst, big, sd, n = stats(pnls)
    print("%-12s tot $%+7.2f | /win $%+.3f | worst $%+6.2f | win<-$2: %3d | std %.2f | /100win $%+.1f" %
          (name, tot, mean, worst, big, sd, mean * 100))


# classify each window's regime by |final-strike| terciles (move magnitude)
moves = sorted(abs(float(w[4][4]) - float(w[0][1])) for w in windows)
q1 = moves[len(moves) // 3]; q2 = moves[2 * len(moves) // 3]
def regime(w):
    m = abs(float(w[4][4]) - float(w[0][1]))
    return "chop" if m <= q1 else ("mid" if m <= q2 else "trend")


N = len(windows)
print("=== NAKED-POLICY BACKTEST — %d windows, half_spread=%.3f ===" % (N, HALF_SPREAD))
print("regime split by |final-strike|: chop<=$%.0f, mid<=$%.0f, trend>$%.0f" % (q1, q2, q2))
print()
print("--- ALL WINDOWS ---")
report("ride", [pnl_window(w, "ride") for w in windows])
for gt in (2, 3, 4):
    report("gate@t=%d" % gt, [pnl_window(w, "gate", gt) for w in windows])

print()
print("--- BY REGIME (ride vs best gate@t=3) ---")
print("%-6s %5s | %-22s | %-22s" % ("regime", "n", "ride (/win, worst, <-2)", "gate@3 (/win, worst, <-2)"))
for reg in ("chop", "mid", "trend"):
    ws = [w for w in windows if regime(w) == reg]
    rp = [pnl_window(w, "ride") for w in ws]
    gp = [pnl_window(w, "gate", 3) for w in ws]
    _, rm, rw, rb, _, n = stats(rp)
    _, gm, gw, gb, _, _ = stats(gp)
    print("%-6s %5d | $%+.3f  worst $%+5.2f  <-2:%3d | $%+.3f  worst $%+5.2f  <-2:%3d" %
          (reg, n, rm, rw, rb, gm, gw, gb))
print()
print("KEY QUESTION: in the 'trend' regime (where we bled live), does gate@3")
print("turn the per-window net less-negative / positive vs ride? That is the real test.")

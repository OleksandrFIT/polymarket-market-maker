"""Improved bot backtest: uses 1m high/low to capture intra-minute fills.
Runs with detector ON and OFF to see if the detector hurts pair-close."""
import urllib.request, json
from quoter.config import Config
from quoter.runner.trend_detector import win_prob_up, sigma_remaining, detect_bias

cfg = Config(trend_confidence=0.40, trend_gate_sec=600.0, trend_vol_fallback=30.0,
             rungs=2, rung_size=5, rung_spacing=0.03, merge_edge=0.02,
             naked_cap=5, min_buy_price=0.42)
UA = {"User-Agent": "Mozilla/5.0"}
kl = []
end = ""
for _ in range(5):
    u = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000"
    if end:
        u += "&endTime=" + str(end)
    part = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25))
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
rungs = [round(0.49 - r * cfg.rung_spacing, 2) for r in range(cfg.rungs)]
rungs = [p for p in rungs if p >= cfg.min_buy_price]

def run(detector_on):
    total = 0.0; pair_w = naked_w = sit_w = 0; pp = npn = 0.0
    for w in windows:
        strike = float(w[0][1]); final = float(w[4][4])
        upf = []; dnf = []; buf = []
        for t in range(5):
            ts = w[t][0] // 1000
            hi = float(w[t][2]); lo = float(w[t][3]); cl = float(w[t][4])
            buf.append((cl, ts))
            time_left = (5 - (t + 1)) * 60
            if time_left <= 0:
                continue
            sig = sigma_remaining(buf, time_left, cfg)
            # intra-minute: Up cheapest at BTC low; Down cheapest at BTC high
            up_price_low = win_prob_up(lo, strike, sig)     # Up price when BTC dips
            dn_price_low = 1.0 - win_prob_up(hi, strike, sig)  # Down price when BTC pops
            bias = detect_bias(cl, strike, sig, time_left, cfg) if detector_on else "NEUTRAL"
            sup_up = (bias == "DOWN"); sup_dn = (bias == "UP")
            if not sup_up:
                for r in rungs:
                    if r not in upf and up_price_low <= r and (len(upf) * 5 + 5 - len(dnf) * 5) <= cfg.naked_cap:
                        upf.append(r)
            if not sup_dn:
                for r in rungs:
                    if r not in dnf and dn_price_low <= r and (len(dnf) * 5 + 5 - len(upf) * 5) <= cfg.naked_cap:
                        dnf.append(r)
        up_sh = len(upf) * 5; dn_sh = len(dnf) * 5
        cost = sum(5 * p for p in upf) + sum(5 * p for p in dnf)
        win = "Up" if final >= strike else "Down"
        payout = (up_sh if win == "Up" else dn_sh) * 1.0
        pnl = payout - cost
        total += pnl
        if up_sh > 0 and dn_sh > 0:
            pair_w += 1; pp += pnl
        elif up_sh == 0 and dn_sh == 0:
            sit_w += 1
        else:
            naked_w += 1; npn += pnl
    n = len(windows)
    print("--- detector %s ---" % ("ON" if detector_on else "OFF"))
    print("  pairs %d (%.0f%%) PnL $%+.2f | naked %d (%.0f%%) PnL $%+.2f | sit %d (%.0f%%)" %
          (pair_w, 100*pair_w/n, pp, naked_w, 100*naked_w/n, npn, sit_w, 100*sit_w/n))
    print("  TOTAL $%+.2f / %d win = $%+.3f/win | /100win $%+.1f (5sh) | /100win $%+.1f (25sh)" %
          (total, n, total/n, total/n*100, total/n*100*5))
    print()

print("=== BOT BACKTEST (high/low fills) — %d windows ===" % len(windows))
run(True)
run(False)

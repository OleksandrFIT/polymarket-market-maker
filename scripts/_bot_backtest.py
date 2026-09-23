"""Full backtest of the CURRENT bot config on historical BTC 5m windows.
Reconstructs price path, approximates Polymarket prices, simulates our bids
filling, detector suppression, naked cap, holds naked (auto_flat off). Read-only."""
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

delta = cfg.merge_edge / 2.0  # 0.01
# rung prices from entry_mid ~0.50: top = 0.50-0.01=0.49, then -0.03 => 0.46
up_rungs = [round(0.49 - r * cfg.rung_spacing, 2) for r in range(cfg.rungs)]   # [0.49,0.46]
dn_rungs = [round(0.49 - r * cfg.rung_spacing, 2) for r in range(cfg.rungs)]
up_rungs = [p for p in up_rungs if p >= cfg.min_buy_price]
dn_rungs = [p for p in dn_rungs if p >= cfg.min_buy_price]

total = 0.0
pair_w = naked_w = sit_w = 0
pair_pnl_sum = naked_pnl_sum = 0.0
for w in windows:
    strike = float(w[0][1])
    final = float(w[4][4])
    up_filled = []  # list of fill prices
    dn_filled = []
    buf = []
    for t in range(5):
        ts = w[t][0] // 1000
        price = float(w[t][4])
        buf.append((price, ts))
        time_left = (5 - (t + 1)) * 60
        if time_left <= 0:
            continue
        sig = sigma_remaining(buf, time_left, cfg)
        up_price = win_prob_up(price, strike, sig)
        dn_price = 1.0 - up_price
        bias = detect_bias(price, strike, sig, time_left, cfg)
        sup_up = (bias == "DOWN")  # Up is loser -> suppressed
        sup_dn = (bias == "UP")
        naked = len(up_filled) * 5 - len(dn_filled) * 5
        # Up bids fill if up_price dropped to the rung (and not suppressed, cap ok)
        if not sup_up:
            for r in up_rungs:
                if r not in [f for f in up_filled] and up_price <= r and (naked + 5) <= cfg.naked_cap + 0:
                    # cap: only if filling keeps naked <= cap (heavy side)
                    if (len(up_filled) * 5 + 5 - len(dn_filled) * 5) <= cfg.naked_cap:
                        up_filled.append(r)
                        naked = len(up_filled) * 5 - len(dn_filled) * 5
        if not sup_dn:
            for r in dn_rungs:
                if r not in [f for f in dn_filled] and dn_price <= r:
                    if (len(dn_filled) * 5 + 5 - len(up_filled) * 5) <= cfg.naked_cap:
                        dn_filled.append(r)
    up_sh = len(up_filled) * 5
    dn_sh = len(dn_filled) * 5
    up_cost = sum(5 * p for p in up_filled)
    dn_cost = sum(5 * p for p in dn_filled)
    win = "Up" if final >= strike else "Down"
    payout = (up_sh if win == "Up" else dn_sh) * 1.0
    pnl = payout - up_cost - dn_cost
    total += pnl
    if up_sh > 0 and dn_sh > 0:
        pair_w += 1
        pair_pnl_sum += pnl
    elif up_sh == 0 and dn_sh == 0:
        sit_w += 1
    else:
        naked_w += 1
        naked_pnl_sum += pnl

n = len(windows)
print("=== CURRENT BOT BACKTEST — %d historical windows ===" % n)
print("pair windows (both filled): %d (%.0f%%) -> PnL $%+.2f" % (pair_w, 100 * pair_w / n, pair_pnl_sum))
print("naked windows (one side):   %d (%.0f%%) -> PnL $%+.2f" % (naked_w, 100 * naked_w / n, naked_pnl_sum))
print("sit-out windows (no fill):  %d (%.0f%%)" % (sit_w, 100 * sit_w / n))
print()
print("TOTAL PnL: $%+.2f   over %d windows   = $%+.3f/window" % (total, n, total / n))
print("projected /100 windows: $%+.1f   (5-share rungs)" % (total / n * 100))
print("projected /100 windows @ 25-share: $%+.1f" % (total / n * 100 * 5))

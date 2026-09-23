"""Backtest the trend detector's accuracy on historical BTC 5m windows (read-only)."""
import urllib.request, json
from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining, win_prob_up

cfg = Config(trend_confidence=0.40, trend_gate_sec=600.0, trend_vol_fallback=30.0)
UA = {"User-Agent": "Mozilla/5.0"}

kl = []
end = ""
for _ in range(4):
    u = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000"
    if end:
        u += "&endTime=" + str(end)
    part = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25))
    kl = part + kl
    end = part[0][0] - 60000
kl = sorted(kl, key=lambda k: k[0])
print("pulled", len(kl), "1m candles")

windows = []
i = 0
while i + 5 <= len(kl):
    t0 = kl[i][0] // 1000
    if (t0 % 300) != 0:
        i += 1
        continue
    windows.append(kl[i:i + 5])
    i += 5
print("reconstructed", len(windows), "5m windows")

hits = tot = 0
pups = []
by_tl = {240: [0, 0], 180: [0, 0], 120: [0, 0], 60: [0, 0]}
for w in windows:
    strike = float(w[0][1])
    final = float(w[4][4])
    actual = "UP" if final >= strike else "DOWN"
    buf = []
    for t in range(5):
        ts = w[t][0] // 1000
        price = float(w[t][4])
        buf.append((price, ts))
        time_left = (5 - (t + 1)) * 60
        if time_left <= 0:
            continue
        sig = sigma_remaining(buf, time_left, cfg)
        pup = win_prob_up(price, strike, sig)
        bias = detect_bias(price, strike, sig, time_left, cfg)
        if bias != "NEUTRAL":
            tot += 1
            correct = (bias == actual)
            if correct:
                hits += 1
            pups.append(pup if bias == "UP" else 1 - pup)
            if time_left in by_tl:
                by_tl[time_left][1] += 1
                if correct:
                    by_tl[time_left][0] += 1

print()
print("=== DETECTOR ACCURACY ===")
print("non-NEUTRAL calls:", tot, "| HIT RATE: %.1f%%" % (100 * hits / max(1, tot)))
print("avg confidence (implied price) when calling: %.1f%%" % (100 * sum(pups) / max(1, len(pups))))
print("  -> hit-rate > confidence  => detector BEATS market (edge, +EV)")
print("  -> hit-rate ~ confidence  => no edge (EV~0, breakeven)")
print("  -> hit-rate < confidence  => detector WORSE (lose money)")
print()
for tl in (240, 180, 120, 60):
    h, n = by_tl[tl]
    if n:
        print("  time_left %3ds: %.0f%% correct (n=%d)" % (tl, 100 * h / n, n))

"""Robustness backtest: many windows across time, split into chronological chunks,
to check if the +EV holds across regimes (not just one lucky trending period)."""
import urllib.request, json, time
from quoter.config import Config
from quoter.runner.trend_detector import win_prob_up, sigma_remaining, detect_bias

cfg = Config(trend_confidence=0.40, trend_gate_sec=600.0, trend_vol_fallback=30.0,
             rungs=2, rung_size=5, rung_spacing=0.03, merge_edge=0.02,
             naked_cap=5, min_buy_price=0.42)
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
rungs = [round(0.49 - r * cfg.rung_spacing, 2) for r in range(cfg.rungs) if round(0.49 - r * cfg.rung_spacing, 2) >= cfg.min_buy_price]

def sim_window(w):
    strike = float(w[0][1]); final = float(w[4][4])
    upf = []; dnf = []; buf = []
    for t in range(5):
        ts = w[t][0] // 1000
        hi = float(w[t][2]); lo = float(w[t][3]); cl = float(w[t][4])
        buf.append((cl, ts))
        tl = (5 - (t + 1)) * 60
        if tl <= 0:
            continue
        sig = sigma_remaining(buf, tl, cfg)
        up_lo = win_prob_up(lo, strike, sig)
        dn_lo = 1.0 - win_prob_up(hi, strike, sig)
        bias = detect_bias(cl, strike, sig, tl, cfg)
        if bias != "DOWN":
            for r in rungs:
                if r not in upf and up_lo <= r and (len(upf)*5+5-len(dnf)*5) <= cfg.naked_cap:
                    upf.append(r)
        if bias != "UP":
            for r in rungs:
                if r not in dnf and dn_lo <= r and (len(dnf)*5+5-len(upf)*5) <= cfg.naked_cap:
                    dnf.append(r)
    up_sh = len(upf)*5; dn_sh = len(dnf)*5
    cost = sum(5*p for p in upf) + sum(5*p for p in dnf)
    win = "Up" if final >= strike else "Down"
    payout = (up_sh if win == "Up" else dn_sh) * 1.0
    kind = "pair" if (up_sh and dn_sh) else ("sit" if not (up_sh or dn_sh) else "naked")
    return payout - cost, kind

N = len(windows)
ts0 = windows[0][0][0] // 1000
tsN = windows[-1][0][0] // 1000
print("=== ROBUSTNESS BACKTEST — %d windows (~%.1f days) ===" % (N, (tsN - ts0) / 86400))
CHUNKS = 6
csz = N // CHUNKS
gtot = 0.0
for ci in range(CHUNKS):
    chunk = windows[ci*csz:(ci+1)*csz] if ci < CHUNKS-1 else windows[ci*csz:]
    tot = 0.0; pk = {"pair": 0, "naked": 0, "sit": 0}
    for w in chunk:
        p, k = sim_window(w); tot += p; pk[k] += 1
    gtot += tot
    n = len(chunk)
    print("chunk %d (%4d win): PnL $%+7.2f = $%+.3f/win | pairs %2.0f%% naked %2.0f%% sit %2.0f%%" %
          (ci+1, n, tot, tot/n, 100*pk["pair"]/n, 100*pk["naked"]/n, 100*pk["sit"]/n))
print()
print("OVERALL: $%+.2f / %d win = $%+.3f/win | /100win $%+.1f (5sh) $%+.1f (25sh)" %
      (gtot, N, gtot/N, gtot/N*100, gtot/N*100*5))

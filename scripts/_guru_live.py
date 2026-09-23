"""LIVE monitor of the guru's every fill, with running per-window balance.

Polls data-api every ~3s, dedupes by (txHash, outcome, side, price, size, ts),
prints each NEW fill as it lands + the window's running totals (Up/Down qty+avg,
pairs, pair cost, naked). Read-only. NOTE: the CLOB is anonymous so we see his
FILLS, not his resting (unfilled) offers — fills are the truthful record of action.
"""
import urllib.request, json, time, collections

UA = {"User-Agent": "Mozilla/5.0"}
ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
POLL = 3.0
MAX_SEC = 7200


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))


def hhmmss(ts):
    return time.strftime("%H:%M:%S", time.gmtime(ts))


seen = set()
# per-window: side -> [qty, cost$]
win = collections.defaultdict(lambda: {"Up": [0.0, 0.0], "Down": [0.0, 0.0]})
start = time.time()
print("=== GURU LIVE MONITOR  %s UTC  (poll %.0fs) ===" % (hhmmss(time.time()), POLL), flush=True)

while time.time() - start < MAX_SEC:
    try:
        rows = get("https://data-api.polymarket.com/activity?user=%s&limit=200&type=TRADE" % ADDR)
    except Exception:
        time.sleep(POLL); continue
    new = []
    for t in rows:
        key = (t.get("transactionHash"), t.get("outcome"), t.get("side"),
               t.get("price"), t.get("size"), t.get("timestamp"))
        if key in seen:
            continue
        seen.add(key)
        new.append(t)
    new.sort(key=lambda t: t.get("timestamp") or 0)
    for t in new:
        slug = t.get("slug") or ""
        if "btc-updown-15m" not in slug:
            continue
        ws = slug.split("-")[-1]            # window start ts
        outc = t.get("outcome"); side = t.get("side")
        px = float(t.get("price") or 0); sz = float(t.get("size") or 0)
        w = win[ws]
        if side == "BUY" and outc in ("Up", "Down"):
            w[outc][0] += sz; w[outc][1] += px * sz
        uq, uc = w["Up"]; dq, dc = w["Down"]
        ua = (uc / uq) if uq else 0; da = (dc / dq) if dq else 0
        pairs = min(uq, dq)
        spent = uc + dc
        paircost = (spent / pairs) if pairs else 0
        naked = uq - dq
        nk = ("Up+%.0f" % naked) if naked > 0 else (("Down+%.0f" % -naked) if naked < 0 else "flat")
        print("[%s] w%s %-4s %-4s %.2f x%-6.1f | Up %.0f@%.3f  Down %.0f@%.3f  pair=%.3f naked=%s spent=$%.1f"
              % (hhmmss(t.get("timestamp")), ws[-4:], side, outc, px, sz,
                 uq, ua, dq, da, paircost, nk, spent), flush=True)
    time.sleep(POLL)
print("=== monitor ended ===", flush=True)

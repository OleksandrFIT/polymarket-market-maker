"""Live parallel comparison: 0xb27b's real 5m-BTC bets (activity: buys both sides / merges /
redeems -> real per-window PnL) vs OUR dry-run bot's decision (regime skip / enter + quotes).
Runs ~MIN minutes, one row per completed window. Writes to stdout (redirect to a file)."""
import json, urllib.request, collections, time, sys
ADDR = "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
LOG = "/home/ubuntu/poly-quoter/logs/control.log"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 20
def get(u, tries=3):
    for _ in range(tries):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
        except Exception: pass
    return []
def his_window(slug):
    """his buys/merge/redeem in a window -> dict."""
    a = get("https://data-api.polymarket.com/activity?user=%s&limit=500" % ADDR)
    d = {"buy": 0.0, "merge": 0.0, "redeem": 0.0, "sh": collections.Counter(), "pxs": []}
    for x in a:
        if x.get("slug") != slug: continue
        t = x["type"]; usd = float(x.get("usdcSize") or 0)
        if t == "TRADE" and x.get("side") == "BUY":
            d["buy"] += usd; d["sh"][x.get("outcome")] += float(x.get("size") or 0)
            d["pxs"].append((x.get("outcome"), float(x.get("price") or 0)))
        elif t == "MERGE": d["merge"] += usd
        elif t == "REDEEM": d["redeem"] += usd
    d["pnl"] = d["merge"] + d["redeem"] - d["buy"]
    return d
def our_window(slug):
    """our bot's decision for a window from control.log (today)."""
    skip = enter = 0; quotes = []
    try:
        for line in open(LOG):
            if slug not in line: continue
            if '"regime_skip"' in line: skip += 1
            if '"topbook_enter"' in line: enter += 1
            if '"topbook_quotes"' in line:
                try:
                    r = json.loads(line); 
                    if r.get("up") and r.get("dn"): quotes.append((r["up"], r["dn"]))
                except Exception: pass
    except Exception: pass
    if skip: return "REGIME-SKIP"
    if enter:
        if quotes:
            up = sum(q[0] for q in quotes)/len(quotes); dn = sum(q[1] for q in quotes)/len(quotes)
            return "traded: bid Up~%.2f Dn~%.2f (sum %.2f)" % (up, dn, up+dn)
        return "entered (no live quotes)"
    return "no-entry"
print("LIVE COMPARE — 0xb27b vs our dry-run bot (5m BTC). runs ~%d min\n" % MIN, flush=True)
print("%-10s │ %-42s │ %s" % ("window", "0xb27b (real)", "OUR bot (dry-run)"), flush=True)
print("-"*100, flush=True)
seen = set(); t_end = time.time() + MIN*60
while time.time() < t_end:
    now = int(time.time()); cur_open = (now//300)*300
    done = cur_open - 300           # last fully-closed window
    slug = "btc-updown-5m-%d" % done
    if slug not in seen and now - done > 90:   # settled enough for his merge/redeem
        seen.add(slug)
        h = his_window(slug)
        sides = "both" if len([s for s,n in h["sh"].items() if n>0])>=2 else ("one" if h["sh"] else "-")
        his = "%s | buy$%.0f merge$%.0f redeem$%.0f | PnL $%+.1f" % (sides, h["buy"], h["merge"], h["redeem"], h["pnl"])
        print("%-10s │ %-42s │ %s" % (slug[-6:], his, our_window(slug)), flush=True)
    time.sleep(20)
print("\n--- done ---", flush=True)

"""Forensic of one hour of wallet 0xb945...db68 — per-window bet detail + PnL.

Pinpoints the hour the user saw on the Polymarket graph (+$179: $273->$452) by
printing a by-hour PnL table (UTC / ET / Kyiv), then drilling into the target hour:
every BUY fill (time, side, size, price), aggregates, winner, realized PnL, and the
pair-vs-directional split. Read-only.

Usage: python3 scripts/_forensic_hour.py [KYIV_HOUR]   (default 14)
"""
import urllib.request, json, time, sys, collections, datetime as dt

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0"}
NOW = int(time.time())
SINCE = NOW - 26 * 3600
TARGET_KYIV_HOUR = int(sys.argv[1]) if len(sys.argv) > 1 else 14
KYIV = 3 * 3600   # EEST = UTC+3
ET = -4 * 3600    # EDT = UTC-4


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.4)


def page(endpoint, limit=500):
    out, off = [], 0
    while True:
        b = get("%s/%s?user=%s&limit=%d&offset=%d" % (BASE, endpoint, WALLET, limit, off))
        if not b:
            break
        keep = [x for x in b if x.get("timestamp", 0) >= SINCE]
        out += keep
        if len([x for x in b if x.get("timestamp", 0) >= SINCE]) < len(b) or len(b) < limit:
            break
        off += limit
        time.sleep(0.1)
    return out


trades = [t for t in page("trades") if t.get("side") == "BUY"]
print("BUY trades in last 26h: %d" % len(trades))

# group by window (conditionId)
W = collections.defaultdict(lambda: {"ts": 0, "slug": "", "up_tok": None,
                                     "fills": [], "up_sh": 0.0, "up_usd": 0.0,
                                     "dn_sh": 0.0, "dn_usd": 0.0})
for t in trades:
    w = W[t["conditionId"]]
    w["slug"] = t.get("slug", "")
    try:
        w["ts"] = int(t["slug"].split("-")[-1])
    except Exception:
        pass
    sz, px = float(t["size"]), float(t["price"])
    up = t.get("outcomeIndex") == 0
    w["fills"].append((t["timestamp"], "Up" if up else "Dn", sz, px))
    if up:
        w["up_sh"] += sz; w["up_usd"] += sz * px
        if not w["up_tok"]:
            w["up_tok"] = t.get("asset")
    else:
        w["dn_sh"] += sz; w["dn_usd"] += sz * px


def resolve(tok, ts, slug):
    if not tok or not ts:
        return None
    tf = 300 if "-5m-" in slug else 900          # 5m vs 15m window length
    r = get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"
            % (tok, ts + tf - 60, ts + tf + 240))
    h = r.get("history", []) if isinstance(r, dict) else []
    if not h:   # fallback: take the very last available print for this token
        r = get("https://clob.polymarket.com/prices-history?market=%s&interval=max&fidelity=1" % tok)
        h = r.get("history", []) if isinstance(r, dict) else []
    return ("Up" if h[-1]["p"] >= 0.5 else "Dn") if h else None


rows = []
for c, w in W.items():
    if not w["ts"]:
        continue
    win = resolve(w["up_tok"], w["ts"], w["slug"])
    spent = w["up_usd"] + w["dn_usd"]
    win_sh = w["up_sh"] if win == "Up" else (w["dn_sh"] if win == "Dn" else 0)
    pnl = (win_sh - spent) if win else None
    pairs = min(w["up_sh"], w["dn_sh"])
    up_avg = w["up_usd"] / w["up_sh"] if w["up_sh"] else 0
    dn_avg = w["dn_usd"] / w["dn_sh"] if w["dn_sh"] else 0
    pair_cost = up_avg + dn_avg
    pair_pnl = pairs * (1 - pair_cost) if pairs else 0
    naked_pnl = (pnl - pair_pnl) if pnl is not None else None
    rows.append({**w, "win": win, "spent": spent, "pnl": pnl,
                 "pairs": pairs, "up_avg": up_avg, "dn_avg": dn_avg,
                 "pair_pnl": pair_pnl, "naked_pnl": naked_pnl})
rows.sort(key=lambda r: r["ts"])


def hh(ts, off):
    return dt.datetime.utcfromtimestamp(ts + off).strftime("%H:%M")


def hour_of(ts, off):
    return (ts + off) // 3600 % 24


# ---- by-hour table (Kyiv) ----
print("\n=== PnL by hour (resolved windows only) ===")
print(" Kyiv  ET    n   spent    PnL")
byhr = collections.defaultdict(lambda: [0, 0.0, 0.0])
for r in rows:
    if r["pnl"] is None:
        continue
    k = hour_of(r["ts"], KYIV)
    byhr[k][0] += 1; byhr[k][1] += r["spent"]; byhr[k][2] += r["pnl"]
for k in sorted(byhr):
    n, sp, p = byhr[k]
    et = (k - 7) % 24
    print(" %02d:00 %02d:00 %3d  $%6.0f  $%+7.1f%s" %
          (k, et, n, sp, p, "   <<< target" if k == TARGET_KYIV_HOUR else ""))

# ---- detail target hour ----
print("\n" + "=" * 64)
print("  DETAIL — Kyiv %02d:00-%02d:00 (ET %02d:00-%02d:00)" %
      (TARGET_KYIV_HOUR, TARGET_KYIV_HOUR + 1, (TARGET_KYIV_HOUR - 7) % 24, (TARGET_KYIV_HOUR - 6) % 24))
print("=" * 64)
tgt = [r for r in rows if hour_of(r["ts"], KYIV) == TARGET_KYIV_HOUR]
if not tgt:
    print("(no windows in that Kyiv hour; try another hour arg)")
tot = 0.0
for r in tgt:
    print("\n  window %s ET / %s Kyiv  (slug %s)" %
          (hh(r["ts"], ET), hh(r["ts"], KYIV), r["slug"]))
    print("    Up : %5.0f sh @ avg %.3f  ($%.0f)" % (r["up_sh"], r["up_avg"], r["up_usd"]))
    print("    Dn : %5.0f sh @ avg %.3f  ($%.0f)" % (r["dn_sh"], r["dn_avg"], r["dn_usd"]))
    print("    spent $%.0f | pairs %.0f @ $%.3f | winner %s" %
          (r["spent"], r["pairs"], r["up_avg"] + r["dn_avg"], r["win"] or "?"))
    if r["pnl"] is not None:
        tot += r["pnl"]
        print("    PnL $%+.1f   (pair $%+.1f + directional/naked $%+.1f)" %
              (r["pnl"], r["pair_pnl"], r["naked_pnl"]))
    # show each fill
    print("    fills (time ET | side | size @ price):")
    for ts, side, sz, px in sorted(r["fills"]):
        print("      %s  %-3s  %5.0f @ %.3f" % (hh(ts, ET), side, sz, px))
print("\n  >>> TARGET HOUR TOTAL PnL: $%+.1f over %d windows" % (tot, len(tgt)))

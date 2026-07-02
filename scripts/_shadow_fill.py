"""SHADOW-FILL: the most realistic pre-live test. Cross the dry-run quoter's REAL intent
logs (dryrun_place lines with timestamps = what the live bot would have resting, at its real
cadence, gates and pair-cost logic) with the window's REAL taker tape → would-be fills,
matched%, shadow-PnL on TODAY'S market. Optimism left: the market doesn't react to us
(no penny-war) and queue-ahead=0 (justified: price improvement puts us alone at our level).
Run ON THE SERVER (has logs + network). Usage: python3 scripts/_shadow_fill.py [control_log]
"""
import sys
import json
import datetime as dt
import urllib.request
import collections

from quoter.research.mm_tape import load_window

LOG = sys.argv[1] if len(sys.argv) > 1 else "logs/control.log"
SIZE, NAKED_CAP, TICK_SEC, END_BUFFER = 5.0, 10.0, 2.0, 12
UA = {"User-Agent": "Mozilla/5.0"}


def _epoch(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ── 1. parse intent logs ──────────────────────────────────────────────────
enters = []                                  # (ts, slug)
places = collections.defaultdict(list)       # token_id -> [(ts, price)]
for line in open(LOG):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    ev = r.get("event")
    if ev == "topbook_enter" and r.get("timestamp"):
        enters.append((_epoch(r["timestamp"]), r["slug"]))
    elif ev == "dryrun_place" and r.get("timestamp"):
        places[r["token_id"]].append((_epoch(r["timestamp"]), float(r["price"])))

slugs = sorted({s for _, s in enters})
print("windows with intent logs:", len(slugs))

# ── 2. token -> (slug, side) via gamma ────────────────────────────────────
tokmap = {}
for slug in slugs:
    try:
        g = json.load(urllib.request.urlopen(urllib.request.Request(
            "https://gamma-api.polymarket.com/markets?slug=%s" % slug, headers=UA), timeout=15))
        toks = json.loads(g[0]["clobTokenIds"])
        tokmap[toks[0]] = (slug, "Up")
        tokmap[toks[1]] = (slug, "Down")
    except Exception:
        continue

# ── 3. shadow-fill each window ────────────────────────────────────────────
rows = []
for slug in slugs:
    w = load_window(slug)
    if not w or not w[0]:
        continue                                            # unresolved yet -> pick up next run
    tape, winner, open_ts = w
    end_ts = open_ts + 300 - END_BUFFER
    # per-side quote timeline from intents
    q = {"Up": [], "Down": []}
    for tid, (s, side) in tokmap.items():
        if s == slug:
            q[side] = sorted(places.get(tid, []))
    if not q["Up"] and not q["Down"]:
        continue
    st = {"Up": [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
          "Down": [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    inv = {"Up": 0.0, "Down": 0.0}
    spent = 0.0
    merged = 0.0
    fills = 0.0

    def active_price(side, t):
        p = None
        for ts_, pr in q[side]:
            if ts_ <= t:
                p = pr
            else:
                break
        return p

    t0 = min(ts for side in ("Up", "Down") for ts, _ in q[side]) if (q["Up"] or q["Down"]) else None
    t = t0
    while t is not None and t < end_ts:
        for side in ("Up", "Down"):
            other = "Down" if side == "Up" else "Up"
            if inv[side] - inv[other] >= NAKED_CAP:          # live skew gate would block this side
                continue
            pr = active_price(side, t)
            if pr is None:
                continue
            v = sum(x["size"] for x in st[side] if t <= x["ts"] < t + TICK_SEC and x["price"] <= pr)
            f = min(SIZE, v)
            if f > 0:
                inv[side] += f
                spent += f * pr
                fills += f
        m = min(inv["Up"], inv["Down"])
        if m > 0:
            merged += m
            inv["Up"] -= m
            inv["Down"] -= m
        t += TICK_SEC
    if spent <= 0:
        rows.append((slug, 0.0, 0.0, 0.0, 0.0, winner))
        continue
    returned = merged + inv[winner]
    rows.append((slug, returned - spent, spent, fills, merged, winner))

print("windows shadow-filled (resolved):", len(rows))
traded = [r for r in rows if r[2] > 0]
if traded:
    tp = sum(r[1] for r in traded)
    ts_ = sum(r[2] for r in traded)
    tf = sum(r[3] for r in traded)
    tm = sum(r[4] for r in traded)
    print("\n=== SHADOW RESULT (today's live market, real quoter intents) ===")
    print("  edge %+0.2f%%   $/window %+0.3f   win-rate %.0f%%" % (
        100 * tp / ts_, tp / len(traded), 100 * sum(1 for r in traded if r[1] > 0) / len(traded)))
    print("  fills/window %.0f sh (sim baseline ~297)   matched %.0f%%   spent/window $%.1f" % (
        tf / len(traded), 100 * 2 * tm / tf if tf else 0, ts_ / len(traded)))
    print("\nper-window:")
    for slug, pnl, sp, f, m, winner in traded:
        print("  %s  pnl %+6.2f  spent %6.2f  fills %4.0f  merged %4.0f  winner %s" %
              (slug[-10:], pnl, sp, f, m, winner))
print("\nCAVEATS: no market reaction to us (penny-war) and queue-ahead=0; fills capped %d/side/%.0fs." % (SIZE, TICK_SEC))

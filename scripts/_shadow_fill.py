"""SHADOW-FILL v2: the most realistic pre-live test. Uses the bot's per-tick
`topbook_quotes` log (EXACT resting-quote state incl. cancels/gates — a place-only log
credits stale toxic fills the live bot dodges; that artifact made v1 read −0.6% while the
same-hours sim read +1.3%) crossed with the window's REAL taker tape → would-be fills,
matched%, shadow-PnL on today's market. Optimism left: no market reaction to us
(penny-war) and queue-ahead=0 (price improvement puts us alone at our level).
Run ON THE SERVER. Usage: python3 scripts/_shadow_fill.py [control_log]
"""
import sys
import json
import datetime as dt
import collections

from quoter.research.mm_tape import load_window

LOG = sys.argv[1] if len(sys.argv) > 1 else "logs/control.log"
SIZE, NAKED_CAP = 5.0, 10.0


def _epoch(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ── 1. parse exact quote-state timeline per window ────────────────────────
ticks = collections.defaultdict(list)        # slug -> [(ts, up_price|None, dn_price|None)]
for line in open(LOG):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    if r.get("event") == "topbook_quotes" and r.get("timestamp"):
        ticks[r["slug"]].append((_epoch(r["timestamp"]), r.get("up"), r.get("dn")))

print("windows with exact quote-state logs:", len(ticks))

# ── 2. shadow-fill each window against its real tape ─────────────────────
rows = []
for slug, tl in sorted(ticks.items()):
    w = load_window(slug)
    if not w or not w[0]:
        continue                              # unresolved yet — picked up on a later run
    tape, winner, _open_ts = w
    st = {"Up": [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
          "Down": [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    tl.sort()
    inv = {"Up": 0.0, "Down": 0.0}
    spent = 0.0
    merged = 0.0
    fills = 0.0
    for i, (ts, up, dn) in enumerate(tl):
        end = tl[i + 1][0] if i + 1 < len(tl) else ts + 2.0
        for side, pr in (("Up", up), ("Down", dn)):
            if pr is None:
                continue                      # bot had NO resting quote this tick (gate/cancel)
            other = "Down" if side == "Up" else "Up"
            if inv[side] - inv[other] >= NAKED_CAP:
                continue                      # live skew gate would block this side
            v = sum(x["size"] for x in st[side] if ts <= x["ts"] < end and x["price"] <= pr)
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
    if spent <= 0:
        continue
    returned = merged + inv[winner]
    rows.append((slug, returned - spent, spent, fills, merged, winner))

print("windows shadow-filled (resolved):", len(rows))
if rows:
    tp = sum(r[1] for r in rows)
    ts_ = sum(r[2] for r in rows)
    tf = sum(r[3] for r in rows)
    tm = sum(r[4] for r in rows)
    print("\n=== SHADOW v2 RESULT (exact quote state x real tape) ===")
    print("  edge %+0.2f%%   $/window %+0.3f   win-rate %.0f%%" % (
        100 * tp / ts_, tp / len(rows), 100 * sum(1 for r in rows if r[1] > 0) / len(rows)))
    print("  fills/window %.0f sh   matched %.0f%%   spent/window $%.1f" % (
        tf / len(rows), 100 * 2 * tm / tf if tf else 0, ts_ / len(rows)))
    print("\nper-window:")
    for slug, pnl, sp, f, m, winner in rows:
        print("  %s  pnl %+6.2f  spent %6.2f  fills %4.0f  merged %4.0f  winner %s" %
              (slug[-10:], pnl, sp, f, m, winner))
print("\nCAVEATS: no market reaction to us (penny-war); queue-ahead=0; fills capped %d/side/tick." % SIZE)

"""1s-vs-2s cadence A/B from ONE dry-run recording.

The recorder logs topbook_quotes at 1s. We shadow-fill (exact quote state x real taker
tape, the _shadow_fill.py model) the SAME windows twice: (a) all 1s ticks, (b) the ticks
downsampled to every-2nd (≈2s). Same windows, same tape, same skew cap -> the ONLY
difference is cadence, so the PnL/cost delta is the pure staleness effect.

2s stales the quote: a downsampled tick's interval spans ~2.4s, so its (older) price
captures fills that a 1s loop would have repriced before. Usage on the SERVER:
  python3 scripts/_cadence_ab.py [control_log] [iso_cutoff]
"""
import sys
import json
import datetime as dt
import collections

from quoter.research.mm_tape import load_window

LOG = sys.argv[1] if len(sys.argv) > 1 else "logs/control.log"
CUTOFF = sys.argv[2] if len(sys.argv) > 2 else "2026-07-05T18:50:00Z"
SIZE, NAKED_CAP = 5.0, 6.0          # live top_book config (same for both cadences)
PER_WINDOW_CAP = 15.0               # committed-capital cap (cost never decremented, as live)


def _epoch(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


CUT = _epoch(CUTOFF)

# ── parse 1s quote timeline per window (only the recorder session) ──
ticks = collections.defaultdict(list)       # slug -> [(ts, up|None, dn|None)]
for line in open(LOG):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    if r.get("event") == "topbook_quotes" and r.get("timestamp"):
        e = _epoch(r["timestamp"])
        if e >= CUT:
            ticks[r["slug"]].append((e, r.get("up"), r.get("dn")))


def shadow(tl, tape, winner):
    """Replay a quote timeline against the real taker tape -> (pnl, spent, fills, merged)."""
    st = {"Up": [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
          "Down": [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    inv = {"Up": 0.0, "Down": 0.0}
    spent = merged = fills = 0.0
    for i, (ts, up, dn) in enumerate(tl):
        end = tl[i + 1][0] if i + 1 < len(tl) else ts + 1.2
        for side, pr in (("Up", up), ("Down", dn)):
            if pr is None:
                continue
            other = "Down" if side == "Up" else "Up"
            if inv[side] + SIZE - inv[other] > NAKED_CAP:      # hard skew gate (both cadences)
                continue
            remaining = PER_WINDOW_CAP - spent                 # committed-cap: cost never decremented
            if remaining <= 0:
                continue
            v = sum(x["size"] for x in st[side] if ts <= x["ts"] < end and x["price"] <= pr)
            f = min(SIZE, v, remaining / pr)                   # never spend past the $15 cap
            if f > 0:
                inv[side] += f
                spent += f * pr
                fills += f
        m = min(inv["Up"], inv["Down"])
        if m > 0:
            merged += m
            inv["Up"] -= m
            inv["Down"] -= m
    returned = merged + inv[winner]
    return returned - spent, spent, fills, merged


def agg(rows, label):
    rows = [r for r in rows if r[2] > 0]                       # spent>0
    if not rows:
        print(f"{label}: no windows with spend")
        return
    tp = sum(r[1] for r in rows); tsp = sum(r[2] for r in rows)
    tf = sum(r[3] for r in rows); tm = sum(r[4] for r in rows)
    print(f"{label}:  edge {100*tp/tsp:+5.2f}%   $/win {tp/len(rows):+.3f}   "
          f"win {100*sum(1 for r in rows if r[1]>0)/len(rows):3.0f}%   "
          f"fills/win {tf/len(rows):4.0f}   matched {100*2*tm/tf if tf else 0:3.0f}%   "
          f"avg fill-price {tsp/tf if tf else 0:.3f}   spent/win ${tsp/len(rows):.1f}   n={len(rows)}")


r1, r2 = [], []
for slug, tl in sorted(ticks.items()):
    tl.sort()
    w = load_window(slug)
    if not w or not w[0]:
        continue                                              # unresolved yet
    tape, winner, _ = w
    if not tape:
        continue
    p1, s1, f1, m1 = shadow(tl, tape, winner)
    p2, s2, f2, m2 = shadow(tl[::2], tape, winner)            # downsample to ~2s
    r1.append((slug, p1, s1, f1, m1)); r2.append((slug, p2, s2, f2, m2))

print(f"recorder windows (>= {CUTOFF}): {len(ticks)}   resolved & shadow-filled: {len(r1)}\n")
agg(r1, "1s (full)   ")
agg(r2, "2s (downsamp)")
print("\nper-window (pnl 1s vs 2s | spent | fills):")
for (sl, p1, s1, f1, _), (_, p2, s2, f2, _) in zip(r1, r2):
    print(f"  {sl[-10:]}  1s {p1:+6.2f}  2s {p2:+6.2f}  Δ{p1-p2:+6.2f} | sp {s1:5.1f}/{s2:5.1f} | fl {f1:3.0f}/{f2:3.0f}")
print("\nCAVEATS: no market reaction to us; queue-ahead=0; small sample; downsample≈2s.")

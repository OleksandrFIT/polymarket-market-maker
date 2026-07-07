"""Residual-formation analysis: correlate 0xb27b's per-window residual/PnL with price TREND.
Hypothesis: residual (unmatched adverse leg) forms in TRENDING windows (async: one leg fills,
the market runs, the other leg can't complete <$1). If confirmed, the lever to minimize
residual is trend detection / regime handling, not more aggressive quoting.
Memory-safe: streams the book file keeping only first/last/lo/hi mid per window.
Usage: python3 scripts/_residual_analysis.py <competitor_jsonl> <book_jsonl>"""
import sys
import json
import collections

COMP = sys.argv[1]
BOOK = sys.argv[2]

comp = collections.defaultdict(lambda: {"buy": 0., "merged": 0., "up": 0., "dn": 0., "ret": 0.})
for l in open(COMP):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    s = r.get("slug", "")
    if not (isinstance(s, str) and s.startswith("btc-updown-5m-")):
        continue
    t = r.get("type")
    sz = float(r.get("size", 0) or 0)
    u = float(r.get("usdcSize", 0) or 0)
    c = comp[s]
    if t == "TRADE" and r.get("side") == "BUY":
        c["buy"] += u
        if r.get("outcome") == "Up":
            c["up"] += sz
        else:
            c["dn"] += sz
    elif t == "TRADE" and r.get("side") == "SELL":
        c["ret"] += u
    elif t == "MERGE":
        c["merged"] += sz
        c["ret"] += u
    elif t == "REDEEM":
        c["ret"] += u


def mid(bk):
    bb = max((float(p) for p, _ in bk["bids"]), default=None)
    ba = min((float(p) for p, _ in bk["asks"]), default=None)
    if bb is None and ba is None:
        return None
    if bb is None:
        return ba
    if ba is None:
        return bb
    return (bb + ba) / 2


bk = collections.defaultdict(lambda: {"first": None, "last": None, "lo": 1.0, "hi": 0.0})
for l in open(BOOK):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    s = r.get("slug")
    if s not in comp:
        continue
    m = mid(r["yes"])
    if m is None:
        continue
    d = bk[s]
    if d["first"] is None:
        d["first"] = m
    d["last"] = m
    d["lo"] = min(d["lo"], m)
    d["hi"] = max(d["hi"], m)

rows = []
for s in comp:
    if s not in bk or bk[s]["first"] is None:
        continue
    c = comp[s]
    b = bk[s]
    if c["buy"] <= 0:
        continue
    bought = c["up"] + c["dn"]
    resid = bought - 2 * c["merged"]                 # unmatched shares (the residual)
    rows.append({"slug": s,
                 "trend": abs(b["last"] - b["first"]),     # net directional move
                 "exc": b["hi"] - b["lo"],                 # max excursion
                 "fdist": abs(b["last"] - 0.5),            # how resolved by end
                 "resid": resid, "residpct": (resid / bought) if bought else 0,
                 "pnl": c["ret"] - c["buy"], "merged": c["merged"]})

rows.sort(key=lambda r: r["slug"])
rows = rows[1:]                                       # drop partial first window
n = len(rows)


def corr(xs, ys):
    m = len(xs)
    mx = sum(xs) / m
    my = sum(ys) / m
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / m
    sx = (sum((x - mx) ** 2 for x in xs) / m) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / m) ** 0.5
    return cov / (sx * sy) if sx * sy else 0.0


print("windows analyzed: %d\n" % n)
rows.sort(key=lambda r: r["trend"])
k = n // 3
for name, grp in (("LOW trend (chop)", rows[:k]), ("MID trend", rows[k:2 * k]), ("HIGH trend", rows[2 * k:])):
    if not grp:
        continue
    g = len(grp)
    print("%-17s | avg move %.3f | resid/win %5.0f (%4.1f%%) | pnl/win $%+7.1f | win%% %.0f" % (
        name, sum(r["trend"] for r in grp) / g, sum(r["resid"] for r in grp) / g,
        100 * sum(r["residpct"] for r in grp) / g, sum(r["pnl"] for r in grp) / g,
        100 * sum(1 for r in grp if r["pnl"] > 0) / g))

print("\ncorrelations (n=%d):" % n)
print("  net move  vs residual : %+.2f" % corr([r["trend"] for r in rows], [r["resid"] for r in rows]))
print("  net move  vs pnl      : %+.2f" % corr([r["trend"] for r in rows], [r["pnl"] for r in rows]))
print("  excursion vs pnl      : %+.2f" % corr([r["exc"] for r in rows], [r["pnl"] for r in rows]))
print("  |final-0.5| vs pnl    : %+.2f" % corr([r["fdist"] for r in rows], [r["pnl"] for r in rows]))
worst = sorted(rows, key=lambda r: r["pnl"])[:5]
print("\n5 WORST windows (pnl):")
for r in worst:
    print("  %s pnl $%+.0f | net move %.3f | excursion %.3f | resid %.0f (%.0f%%)" % (
        r["slug"][-10:], r["pnl"], r["trend"], r["exc"], r["resid"], 100 * r["residpct"]))

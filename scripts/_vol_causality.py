"""Causality test for a vol-gate: does EARLY-window volatility (first 150s only — causal)
predict LATE excursion and 0xb27b's window loss? If yes, a causal gate is viable (measure
early vol, shrink/skip when high). If early vol has no predictive power, a gate is impossible.
Candidate early predictors: early excursion (hi-lo), early std of mid, early direction reversals.
Memory-safe streaming. Usage: python3 scripts/_vol_causality.py <competitor_jsonl> <book_jsonl>"""
import sys
import json
import collections

COMP = sys.argv[1]
BOOK = sys.argv[2]
SPLIT = 150                                            # first half of the 300s window

# competitor per-window pnl (from activity, no book needed)
cp = collections.defaultdict(lambda: {"buy": 0., "ret": 0.})
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
    u = float(r.get("usdcSize", 0) or 0)
    if t == "TRADE" and r.get("side") == "BUY":
        cp[s]["buy"] += u
    elif t in ("MERGE", "REDEEM") or (t == "TRADE" and r.get("side") == "SELL"):
        cp[s]["ret"] += u


def mid(bk):
    bb = max((float(p) for p, _ in bk["bids"]), default=None)
    ba = min((float(p) for p, _ in bk["asks"]), default=None)
    if bb is None and ba is None:
        return None
    return ba if bb is None else (bb if ba is None else (bb + ba) / 2)


# per window streaming accumulators
W = collections.defaultdict(lambda: {"e_lo": 1., "e_hi": 0., "e_sum": 0., "e_sq": 0., "e_n": 0,
                                      "e_prev": None, "e_dir": 0, "e_rev": 0,
                                      "l_lo": 1., "l_hi": 0.})
for l in open(BOOK):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    s = r.get("slug")
    if s not in cp:
        continue
    m = mid(r["yes"])
    if m is None:
        continue
    open_ts = int(s.rsplit("-", 1)[1])
    elapsed = r["ts"] - open_ts
    d = W[s]
    if elapsed < SPLIT:
        d["e_lo"] = min(d["e_lo"], m)
        d["e_hi"] = max(d["e_hi"], m)
        d["e_sum"] += m
        d["e_sq"] += m * m
        d["e_n"] += 1
        if d["e_prev"] is not None:
            step = m - d["e_prev"]
            sd = 1 if step > 1e-6 else (-1 if step < -1e-6 else 0)
            if sd != 0:
                if d["e_dir"] != 0 and sd != d["e_dir"]:
                    d["e_rev"] += 1
                d["e_dir"] = sd
        d["e_prev"] = m
    else:
        d["l_lo"] = min(d["l_lo"], m)
        d["l_hi"] = max(d["l_hi"], m)

rows = []
for s, d in W.items():
    if d["e_n"] < 3 or d["l_hi"] < d["l_lo"]:
        continue
    if cp[s]["buy"] <= 0:
        continue
    e_std = max(0.0, d["e_sq"] / d["e_n"] - (d["e_sum"] / d["e_n"]) ** 2) ** 0.5
    rows.append({"slug": s, "e_exc": d["e_hi"] - d["e_lo"], "e_std": e_std, "e_rev": d["e_rev"],
                 "l_exc": d["l_hi"] - d["l_lo"], "pnl": cp[s]["ret"] - cp[s]["buy"]})

rows.sort(key=lambda r: r["slug"])
rows = rows[1:]                                        # drop partial first window
n = len(rows)


def corr(xs, ys):
    m = len(xs)
    mx = sum(xs) / m
    my = sum(ys) / m
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / m
    sx = (sum((x - mx) ** 2 for x in xs) / m) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / m) ** 0.5
    return cov / (sx * sy) if sx * sy else 0.0


print("windows: %d\n" % n)
print("CAUSAL predictive power (early half -> outcome):")
print("  early excursion -> late excursion : %+.2f" % corr([r["e_exc"] for r in rows], [r["l_exc"] for r in rows]))
print("  early std       -> late excursion : %+.2f" % corr([r["e_std"] for r in rows], [r["l_exc"] for r in rows]))
print("  early reversals -> late excursion : %+.2f" % corr([r["e_rev"] for r in rows], [r["l_exc"] for r in rows]))
print("  early excursion -> his pnl        : %+.2f" % corr([r["e_exc"] for r in rows], [r["pnl"] for r in rows]))
print("  early std       -> his pnl        : %+.2f" % corr([r["e_std"] for r in rows], [r["pnl"] for r in rows]))
print("  early reversals -> his pnl        : %+.2f" % corr([r["e_rev"] for r in rows], [r["pnl"] for r in rows]))

# gate simulation: bucket by early excursion (the causal signal), show late outcome
rows.sort(key=lambda r: r["e_exc"])
k = n // 3
print("\nGATE SIM — bucket by EARLY excursion (causal):")
print("  %-14s | early exc | late exc | his pnl/win | win%%" % "bucket")
for name, grp in (("LOW early-vol", rows[:k]), ("MID", rows[k:2 * k]), ("HIGH early-vol", rows[2 * k:])):
    if not grp:
        continue
    g = len(grp)
    print("  %-14s | %.3f     | %.3f    | $%+7.1f    | %.0f" % (
        name, sum(r["e_exc"] for r in grp) / g, sum(r["l_exc"] for r in grp) / g,
        sum(r["pnl"] for r in grp) / g, 100 * sum(1 for r in grp if r["pnl"] > 0) / g))

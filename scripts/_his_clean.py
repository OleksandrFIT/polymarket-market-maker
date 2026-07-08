"""Clean residual analysis of 0xb27b: keep only FULLY-collected windows (buy/merged in a sane
range -> partial windows with missing early buys are dropped), recompute the PnL decomposition,
and dissect the WINNER-TILT mechanism: how much + at what PRICES + WHEN he buys the eventual
winner vs loser side (the chase signature).
Usage: python3 scripts/_his_clean.py <competitor_jsonl>"""
import sys
import json
import collections
import statistics as st

from quoter.research.mm_tape import load_window

COMP = sys.argv[1]
SPLIT = 150

comp = collections.defaultdict(list)
for l in open(COMP):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except json.JSONDecodeError:
        continue
    s = r.get("slug", "")
    if isinstance(s, str) and s.startswith("btc-updown-5m-"):
        comp[s].append(r)

clean = []
dropped = 0
for slug, evs in sorted(comp.items()):
    w = load_window(slug)
    if not w or w[1] not in ("Up", "Down"):
        continue
    winner = w[1]
    loser = "Down" if winner == "Up" else "Up"
    open_ts = int(slug.rsplit("-", 1)[1])
    inv = {"Up": 0.0, "Down": 0.0}
    buy = merged = merge_ret = redeem_ret = sell_ret = 0.0
    cu = up = cdn = dn = 0.0
    win_sh = win_cost = los_sh = los_cost = 0.0
    win_late = 0.0            # winner-side shares bought in 2nd half
    win_hi = 0.0              # winner-side shares bought at price >= 0.70 (chase)
    los_lo = 0.0             # loser-side shares bought at price <= 0.30 (cheap completion)
    for e in sorted(evs, key=lambda x: x.get("timestamp", 0)):
        t = e.get("type")
        sz = float(e.get("size", 0) or 0)
        u = float(e.get("usdcSize", 0) or 0)
        px = float(e.get("price", 0) or 0)
        out = e.get("outcome")
        el = e.get("timestamp", 0) - open_ts
        if t == "TRADE" and e.get("side") == "BUY":
            buy += u
            if out == "Up":
                up += sz; cu += u
            else:
                dn += sz; cdn += u
            if out == winner:
                win_sh += sz; win_cost += u
                if el >= SPLIT:
                    win_late += sz
                if px >= 0.70:
                    win_hi += sz
            elif out == loser:
                los_sh += sz; los_cost += u
                if px <= 0.30:
                    los_lo += sz
        elif t == "TRADE" and e.get("side") == "SELL":
            sell_ret += u
        elif t == "MERGE":
            merged += sz; merge_ret += u
        elif t == "REDEEM":
            redeem_ret += u
    if merged <= 20 or buy <= 0:
        continue
    bpm = buy / merged
    if not (0.92 <= bpm <= 1.25):          # partial-collection filter (missing buys -> low bpm)
        dropped += 1
        continue
    pair_cost = (cu / up if up else 0) + (cdn / dn if dn else 0)
    merge_pnl = merged * (1.0 - pair_cost)
    total_pnl = (merge_ret + redeem_ret + sell_ret) - buy
    clean.append({
        "merge_pnl": merge_pnl, "residual_pnl": total_pnl - merge_pnl, "total_pnl": total_pnl,
        "pair_cost": pair_cost, "merged": merged, "bpm": bpm,
        "win_sh": win_sh, "los_sh": los_sh,
        "win_avg": (win_cost / win_sh if win_sh else 0), "los_avg": (los_cost / los_sh if los_sh else 0),
        "win_late_frac": (win_late / win_sh if win_sh else 0),
        "win_hi_frac": (win_hi / win_sh if win_sh else 0),
        "los_lo_frac": (los_lo / los_sh if los_sh else 0),
        "tilt": win_sh - los_sh})

n = len(clean)
print("CLEAN windows: %d  (dropped %d partial)\n" % (n, dropped))
if n == 0:
    sys.exit()


def m(k):
    return sum(r[k] for r in clean) / n


print("PNL DECOMPOSITION (clean, per window mean):")
print("  merge_pnl (spread edge): $%+.2f" % m("merge_pnl"))
print("  residual_pnl (the leg):  $%+.2f" % m("residual_pnl"))
print("  total_pnl:               $%+.2f   (his lb-api ~ $0.2-2.6/window)" % m("total_pnl"))
print("  pair_cost mean $%.4f | buy/merged mean %.3f" % (m("pair_cost"), m("bpm")))

print("\nWINNER-TILT MECHANISM (how he ends net-long the winner):")
print("  winner-side shares bought/win:  %.0f  @ avg $%.3f" % (m("win_sh"), m("win_avg")))
print("  loser-side shares bought/win:   %.0f  @ avg $%.3f" % (m("los_sh"), m("los_avg")))
print("  net tilt (winner - loser):      %+.0f shares/window" % m("tilt"))
print("  windows net-long winner:        %d/%d = %.0f%%" % (sum(1 for r in clean if r["tilt"] > 0), n, 100 * sum(1 for r in clean if r["tilt"] > 0) / n))

print("\nCHASE SIGNATURE:")
print("  winner-side bought at px>=0.70:  %.0f%% of winner buys  (high = chasing the rise)" % (100 * m("win_hi_frac")))
print("  winner-side bought in 2nd half:  %.0f%% of winner buys  (late = following trend)" % (100 * m("win_late_frac")))
print("  loser-side bought at px<=0.30:   %.0f%% of loser buys   (cheap completion of faller)" % (100 * m("los_lo_frac")))

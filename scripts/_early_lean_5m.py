"""Faithful sim of the NEW-bot mechanism on 5m windows (reuses cached paths).

Policy: in the EARLY minutes (0,1,2) buy BOTH sides at the current mid, but LEAN into
the minute's leader (buy `lean` shares of the leader, 1 of the laggard). Hold to
resolution. This accumulates more of the persistent early mover (winner ~73%) while
holding the loser cheap as a hedge — the observed tactic. Measure edge vs lean ratio.
Maker entry (fill at mid, 0 spread) + fee sensitivity. Read-only, no network.
"""
import os, json, statistics, collections

PCACHE = "/tmp/poly_path5_cache"
EARLY = (0, 1, 2)          # early accumulation minutes
FEE = 0.0                  # maker (0 spread); sensitivity added below

wins = []
for fn in os.listdir(PCACHE):
    try:
        d = json.load(open(os.path.join(PCACHE, fn)))
    except Exception:
        d = None
    if d and d.get("up") and d.get("winner"):
        wins.append(d)
print("cached 5m windows: %d\n" % len(wins))


def sim(lean, fee=0.0):
    tot_pnl = tot_spent = 0.0
    we, le = [], []            # winner/loser avg entry across windows
    win_windows = 0
    per = []
    for w in wins:
        up = w["up"]; inv = {"Up": 0.0, "Dn": 0.0}; cost = {"Up": 0.0, "Dn": 0.0}
        for m in EARLY:
            p = up[m]
            leader = "Up" if p > 0.5 else "Dn"
            lp = (p if leader == "Up" else 1 - p) + fee
            laggard = "Dn" if leader == "Up" else "Up"
            gp = (1 - (p if leader == "Up" else 1 - p)) + fee
            inv[leader] += lean; cost[leader] += lean * lp
            inv[laggard] += 1;   cost[laggard] += 1 * gp
        spent = cost["Up"] + cost["Dn"]
        win = "Up" if w["winner"] == "Up" else "Dn"
        pnl = inv[win] - spent
        tot_pnl += pnl; tot_spent += spent
        per.append(pnl)
        if pnl > 0: win_windows += 1
        wsh = inv[win]; lsh = inv["Dn" if win == "Up" else "Up"]
        if wsh: we.append(cost[win] / wsh)
        if lsh: le.append(cost["Dn" if win == "Up" else "Up"] / lsh)
    n = len(wins)
    return {"lean": lean, "pnl_win": tot_pnl / n, "pct": 100 * tot_pnl / tot_spent,
            "winrate_win": 100 * win_windows / n, "we": statistics.mean(we),
            "le": statistics.mean(le), "sharpe": (tot_pnl / n) / statistics.pstdev(per)}


print("=== early two-sided + lean into the mover (maker, 0 spread) ===")
print(" lean(L:1)  PnL/win  %ofspend  win-win%%  winEntry loserEntry  sharpe-ish")
for lean in (1, 2, 3, 5, 8):
    r = sim(lean)
    print("  %d:1      %+.4f   %+.2f%%    %4.0f%%    %.3f    %.3f      %.3f" %
          (r["lean"], r["pnl_win"], r["pct"], r["winrate_win"], r["we"], r["le"], r["sharpe"]))

print("\n=== fee sensitivity at lean 3:1 (does it survive spread cost?) ===")
for fee in (0.0, 0.005, 0.01, 0.02):
    r = sim(3, fee)
    print("  fee %.1f¢:  %+.2f%% of spend  (PnL/win %+.4f)  %s" %
          (fee * 100, r["pct"], r["pnl_win"], "+EV" if r["pct"] > 0 else "-EV"))
print("\nCompare to competitor: winner-entry ~0.61, loser ~0.44, heavy=winner 73%, edge ~0.4%% of vol.")

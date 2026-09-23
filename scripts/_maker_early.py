"""Test the EARLY-DEEP two-sided maker (reverse-engineered from 0xb27b): rest bids on BOTH
sides at mid-DEPTH only in the first ENTRY_SEC of the window (catch both legs near 0.50
before the directional move), merge, hold residual to resolution. Sweep depth x entry to see
if the pair cost drops < $1 (his ~0.93) and the maker flips positive. vs our old best+tick."""
import sys, collections, time
from quoter.research.mm_tape import load_window
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
SIZE, NAKED_CAP, PWC, WIN = 5.0, 6.0, 15.0, 300
OI = {"Up": 0, "Down": 1}
NOW = int(time.time()); BASE = (NOW // 300) * 300 - 600

def sim(tape, winner, ots, depth, entry_sec):
    last = {0: 0.5, 1: 0.5}
    inv = {"Up": 0.0, "Down": 0.0}; cost = {"Up": 0.0, "Down": 0.0}; held = {"Up": 0.0, "Down": 0.0}
    merged = pair_ret = pair_cost = 0.0
    bt = collections.defaultdict(list)
    for x in tape:
        if ots <= x["ts"] < ots + WIN: bt[int((x["ts"] - ots) // 2)].append(x)
    for k in range(WIN // 2):
        sec = k * 2
        quote = {}
        if sec < entry_sec:
            for side in ("Up", "Down"):
                other = "Down" if side == "Up" else "Up"
                if inv[side] + SIZE - inv[other] > NAKED_CAP: continue
                p = round(last[OI[side]] - depth, 3)
                if cost["Up"] + cost["Down"] + p * SIZE > PWC: continue
                if 0 < p < 0.99: quote[side] = p
            if len(quote) == 2 and quote["Up"] + quote["Down"] > 0.999:
                del quote[max(quote, key=lambda s: quote[s])]
        vol = {"Up": 0.0, "Down": 0.0}
        for x in bt.get(k, []):
            last[x["oi"]] = x["price"]
            side = "Up" if x["oi"] == 0 else "Down"
            if x["side"] == "SELL" and side in quote and x["price"] <= quote[side]:
                vol[side] += x["size"]
        for side in ("Up", "Down"):
            if side in quote and vol[side] > 0:
                f = min(SIZE, vol[side]); inv[side] += f; cost[side] += f * quote[side]; held[side] += f * quote[side]
        m = min(inv["Up"], inv["Down"])
        if m > 0:
            pc = 0.0
            for s in ("Up", "Down"):
                a = held[s] / inv[s] if inv[s] > 0 else 0.0
                pc += m * a; held[s] -= m * a; inv[s] -= m
            merged += m; pair_ret += m; pair_cost += pc
    ret = pair_ret + inv[winner] * 1.0
    pnl = ret - (cost["Up"] + cost["Down"])
    return pnl, (pair_cost / merged if merged else None), merged

def main():
    slugs = [BASE - k * 300 for k in range(N)]
    wins = []
    for ots in slugs:
        w = load_window("btc-updown-5m-%d" % ots)
        if w and w[0] and w[1]: wins.append((w[0], w[1], ots))
    print("windows: %d\n" % len(wins))
    print("depth │ entry │ avg PnL │ %win │ avg pair-cost │ pair<$1 │ merged/win")
    print("─" * 72)
    for entry in (60, 120, 300):
        for depth in (0.01, 0.02, 0.03):
            pnls = []; pcs = []; mg = []
            for tape, winner, ots in wins:
                p, pc, m = sim(tape, winner, ots, depth, entry)
                pnls.append(p);  mg.append(m)
                if pc is not None: pcs.append(pc)
            n = len(pnls)
            print("%.2f  │ %3ds  │ %+6.2f │ %3.0f%% │    %.3f     │  %3.0f%%  │ %.1f"
                  % (depth, entry, sum(pnls)/n, 100*sum(1 for x in pnls if x>0)/n,
                     sum(pcs)/len(pcs) if pcs else 0, 100*sum(1 for c in pcs if c<1)/len(pcs) if pcs else 0,
                     sum(mg)/n))
main()

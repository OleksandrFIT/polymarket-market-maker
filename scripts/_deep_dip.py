"""DEEP-DIP catching on real 15m windows — the guru's actual move.

From his tape: he accumulates the underdog at 0.03-0.16 (catching crashes) and
holds/scalps. Two DISTINCT edges, tested separately:

  (1) HOLD-to-resolution: buy a side at its deep dip (its minimum in the window)
      — does that side then WIN more often than its dip price? (favorite-longshot
      at the extreme). edge = win_rate - dip_price.

  (2) INTRA-WINDOW BOUNCE: after a side over-crashes to its minimum, does it
      trade back UP within the window — so a maker who caught the dip can SELL
      higher REGARDLESS of who wins? This is a direction-independent scalp of
      the over-crash. measured = max price AFTER the dip minute - dip price.

For each window and each side we take the MINIMUM price the side reaches over
minutes [lo,hi), whether the side won, and the max price after that minute.
Reuses the 15m path cache. Read-only, no live.
"""
import json, os, collections, statistics

PCACHE = "/tmp/poly_path15_cache"
LO, HI = 3, 13          # entry window (skip the noisy first/last couple minutes)

wins = []
for fn in os.listdir(PCACHE):
    try:
        d = json.load(open(os.path.join(PCACHE, fn)))
    except Exception:
        d = None
    if d and d.get("up") and d.get("winner"):
        wins.append(d)
print("loaded %d cached 15m windows  (entry window min %d-%d)\n" % (len(wins), LO, HI))

# CAUSAL resting-bid fill: a maker bid sits at level L; it fills the FIRST minute
# the side's price drops to <= L (a taker sells into it). No look-ahead — we only
# know the future AFTER the fill. Record: won? and the best price reached AFTER
# the fill (what we could sell the rebound at). If it keeps crashing, best<L => stuck.
LEVELS = [0.05, 0.08, 0.10, 0.15, 0.20]

def causal_fills(L):
    """list of (won, best_sell_after_fill) for every side that touched <=L."""
    out = []
    for w in wins:
        up = w["up"]
        for side in ("Up", "Down"):
            path = [up[m] if side == "Up" else 1 - up[m] for m in range(15)]
            fill_m = None
            for m in range(LO, HI):
                if path[m] <= L:
                    fill_m = m
                    break
            if fill_m is None:
                continue
            after = path[fill_m + 1:14]               # strictly after fill, before settle
            best_after = max(after) if after else path[fill_m]
            out.append((w["winner"] == side, best_after))
    return out

print("=== (1) HOLD: rest a bid at L, fill on first touch, hold to resolution ===")
print(" bidL    n_fills   winrate   edge(win-L)   verdict")
for L in LEVELS:
    f = causal_fills(L)
    n = len(f)
    wr = sum(1 for won, _ in f if won) / n
    edge = wr - L
    tag = "<<< +EV" if edge >= 0.03 else ("~ok" if edge > -0.01 else "-EV (falling knife)")
    print(" %.2f    %5d     %5.1f%%   %+.3f        %s" % (L, n, 100*wr, edge, tag))

print("\n=== (2) BOUNCE: after a fill at L, can we SELL the rebound? (dir-independent) ===")
print(" bidL    n_fills   %sell>=L+3c   %sell>=L+5c   avg(best-L)   scalpEV*")
for L in LEVELS:
    f = causal_fills(L)
    n = len(f)
    deltas = [best - L for _, best in f]
    p3 = 100 * sum(1 for d in deltas if d >= 0.03) / n
    p5 = 100 * sum(1 for d in deltas if d >= 0.05) / n
    avg = statistics.mean(deltas)
    # scalp policy: buy at L, sell at L+0.03 if it ever bounces there, else hold to
    # resolution (win => +(1-L), lose => -L). honest causal EV per share:
    ev = 0.0
    for won, best in f:
        if best - L >= 0.03:
            ev += 0.03
        else:
            ev += (1 - L) if won else -L
    ev /= n
    print(" %.2f    %5d     %5.0f%%        %5.0f%%        %+.3f       %+.3f" % (L, n, p3, p5, avg, ev))

print("\nHOLD edge = win-rate of a side that touched L, minus L (mispriced if > L).")
print("BOUNCE/scalpEV = buy at L, sell at L+3c on a rebound else ride to settle. Causal, no look-ahead.")

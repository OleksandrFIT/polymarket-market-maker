"""Win-rate vs ENTRY PRICE on real 15m windows (the article's core rule).

For every (side, minute) observation we have the Polymarket price and whether
that side eventually WON. Bucket by price; per bucket compute the realized
win-rate and the edge = win_rate - price. The article: an entry is +EV only
where win_rate exceeds the price by >=5c. This shows WHERE the edge actually
lives (cheap entries? expensive favorites? neither = efficient market).

Reuses the 15m path cache. Read-only, no live.
"""
import json, os, collections, statistics

PCACHE = "/tmp/poly_path15_cache"

wins = []
for fn in os.listdir(PCACHE):
    try:
        d = json.load(open(os.path.join(PCACHE, fn)))
    except Exception:
        d = None
    if d and d.get("up") and d.get("winner"):
        wins.append(d)
print("loaded %d cached 15m windows\n" % len(wins))


def collect(minute_lo, minute_hi):
    """(price, won) for each side over minutes [lo,hi)."""
    obs = []
    for w in wins:
        for m in range(minute_lo, minute_hi):
            up = w["up"][m]
            obs.append((up, w["winner"] == "Up"))          # buy Up @ up
            obs.append((1 - up, w["winner"] == "Down"))    # buy Down @ (1-up)
    return obs


def report(obs, label):
    buckets = collections.defaultdict(lambda: [0, 0])   # price-bucket -> [n, wins]
    for price, won in obs:
        b = int(price * 20) / 20.0                      # 0.05 buckets
        buckets[b][0] += 1
        buckets[b][1] += 1 if won else 0
    print("=== %s (%d obs) ===" % (label, len(obs)))
    print("entryPx  n      winrate   edge(win-px)   +EV?")
    for b in sorted(buckets):
        n, wn = buckets[b]
        if n < 30:
            continue
        wr = wn / n
        mid = b + 0.025
        edge = wr - mid
        tag = "<<< +EV (>5c)" if edge >= 0.05 else ("(>0)" if edge > 0 else "")
        print("%.2f-%.2f %5d   %5.1f%%   %+.3f         %s" % (b, b+0.05, n, 100*wr, edge, tag))
    print()


# the actionable entry window the guru uses (~min 8-13) and an early one
report(collect(8, 13), "ENTRY minutes 8-13 (guru's zone)")
report(collect(3, 7), "ENTRY minutes 3-7 (early)")
print("edge = realized win-rate - entry price. >=+0.05 => buying at that price is +EV")
print("(article: 'win rate must exceed entry price by 5c'). Shows WHERE the edge lives.")

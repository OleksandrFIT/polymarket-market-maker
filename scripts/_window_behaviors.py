"""Classify BTC-5m windows by the Up-price PATH shape over the window, count the most frequent
behaviors, and export representative real paths. Streams book jsonl (memory-safe).
Run: python3 scripts/_window_behaviors.py <book_jsonl> [more_jsonl...]"""
import sys
import json
import collections

GRID = list(range(0, 301, 20))          # resample times 0,20,...,300s


def up_mid(book):
    bb = max((float(p) for p, _ in book.get("bids", [])), default=None)
    ba = min((float(p) for p, _ in book.get("asks", [])), default=None)
    if bb is None and ba is None:
        return None
    return ba if bb is None else (bb if ba is None else (bb + ba) / 2)


def resample(pts):
    """pts: sorted [(rel_t, mid)] -> mid at each GRID time via linear interp (clamped ends)."""
    if len(pts) < 3:
        return None
    out = []
    j = 0
    for g in GRID:
        while j + 1 < len(pts) and pts[j + 1][0] <= g:
            j += 1
        if g <= pts[0][0]:
            out.append(pts[0][1])
        elif g >= pts[-1][0]:
            out.append(pts[-1][1])
        else:
            (t0, m0), (t1, m1) = pts[j], pts[min(j + 1, len(pts) - 1)]
            out.append(m0 if t1 == t0 else m0 + (m1 - m0) * (g - t0) / (t1 - t0))
    return out


def classify(u):
    """Label the path shape. u = resampled Up-mid over the window."""
    end = u[-1]
    winner = "Up" if end >= 0.5 else "Down"
    sgn = [1 if x >= 0.5 else -1 for x in u]
    crosses = sum(1 for i in range(len(sgn) - 1) if sgn[i] != sgn[i + 1])
    rng = max(u) - min(u)
    # adverse excursion: how far it went to the LOSER side
    adverse = (0.5 - min(u)) if winner == "Up" else (max(u) - 0.5)
    # commit time: first grid idx where it reaches the winning side by >0.25 and stays
    commit = None
    for i in range(len(u)):
        ok = (u[i] >= 0.75) if winner == "Up" else (u[i] <= 0.25)
        if ok and all((u[k] >= 0.5) == (winner == "Up") for k in range(i, len(u))):
            commit = GRID[i]
            break
    if rng < 0.30 and max(abs(x - 0.5) for x in u[:-2]) < 0.22:
        shape = "grind-tight"                         # stayed near 50/50, narrow resolve
    elif crosses >= 2:
        shape = "chop"                                # whipsaw, multiple crossings
    elif crosses == 1:
        shape = "reversal"                            # went to loser first, reversed
    elif commit is not None and commit <= 80 and adverse < 0.12:
        shape = "early-trend"                         # committed early, clean
    elif commit is None or commit >= 180:
        shape = "late-break"                          # flat then broke late
    else:
        shape = "mid-trend"
    return "%s %s" % (shape, winner)


def main():
    per = collections.defaultdict(list)
    for path in sys.argv[1:]:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            sl = r.get("slug", "")
            if not sl.startswith("btc-updown-5m-"):
                continue
            ts0 = int(sl.rsplit("-", 1)[1])
            m = up_mid(r.get("yes", {}))
            if m is not None:
                per[ts0].append((r["ts"] - ts0, m))

    counts = collections.Counter()
    samples = {}                                      # label -> a representative resampled path
    n = 0
    for ts0, pts in per.items():
        pts.sort()
        u = resample(pts)
        if u is None:
            continue
        lab = classify(u)
        counts[lab] += 1
        n += 1
        if lab not in samples:                        # keep first clean example per label
            samples[lab] = [round(x, 3) for x in u]

    print("windows classified: %d\n" % n)
    print("=== TOP behaviors (Up-price path shape) ===")
    for lab, c in counts.most_common(12):
        print("  %-22s %4d  (%4.1f%%)" % (lab, c, 100 * c / n))
    print("\n=== SAMPLE PATHS (Up-mid at t=0,20,...,300s) ===")
    print("grid: %s" % GRID)
    for lab, _ in counts.most_common(12):
        print("  %-22s %s" % (lab, samples[lab]))


if __name__ == "__main__":
    main()

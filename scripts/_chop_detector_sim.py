"""CAUSAL chop-detector + maker-both sim, memory-safe (compresses snaps to top-of-book, processes
one book file at a time). Decide at t~100s from the Up-price path SO FAR whether to trade
(contested/oscillating = chop-likely) or skip (already committed = trend-likely), then run
maker-both (top_book shadow-fill) only on traded windows. Measures the pair the gate captures.
Run: POLY_MM_CACHE=... python3 scripts/_chop_detector_sim.py <book_jsonl> [more...]"""
import sys
import json
import collections
import statistics as st

from quoter.research.mm_tape import load_window
from quoter.research.exec_ab import top_book_window

GRID = list(range(0, 301, 20))
EARLY = 6                                 # first 6 grid points = 0..100s (causal decision window)


def _tob(book):
    """Compress a book to top-of-book: best bid, best ask (or None)."""
    bb = max((float(p) for p, _ in book.get("bids", [])), default=None)
    ba = min((float(p) for p, _ in book.get("asks", [])), default=None)
    return bb, ba


def _mid_bb_ba(bb, ba):
    if bb is None and ba is None:
        return None
    return ba if bb is None else (bb if ba is None else (bb + ba) / 2)


def resample(pts):
    if len(pts) < 3:
        return None
    out, j = [], 0
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


def regime(u):                            # actual (full path) — validation only
    sgn = [1 if x >= 0.5 else -1 for x in u]
    crosses = sum(1 for i in range(len(sgn) - 1) if sgn[i] != sgn[i + 1])
    return "chop" if crosses >= 2 else ("reversal" if crosses == 1 else "trend")


def detect_trade(u):                      # CAUSAL: decide from first 100s only
    e = u[:EARLY]
    sgn = [1 if x >= 0.5 else -1 for x in e]
    crosses = sum(1 for i in range(len(sgn) - 1) if sgn[i] != sgn[i + 1])
    dev = max(abs(x - 0.5) for x in e)
    return crosses >= 1 or dev < 0.28


def process_file(path, rows):
    """Stream one file -> compressed per-window snaps -> run -> append rows. Then free."""
    mids = collections.defaultdict(list)         # slug -> [(rel_t, mid)]
    snaps = collections.defaultdict(list)        # slug -> [minimal snap]
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
        ybb, yba = _tob(r.get("yes", {}))
        nbb, nba = _tob(r.get("no", {}))
        m = _mid_bb_ba(ybb, yba)
        if m is not None:
            mids[ts0].append((r["ts"] - ts0, m))
        snaps[sl].append({"ts": r["ts"],
                          "yes": {"bids": [[ybb, "500"]] if ybb is not None else [],
                                  "asks": [[yba, "500"]] if yba is not None else []},
                          "no": {"bids": [[nbb, "500"]] if nbb is not None else [],
                                 "asks": [[nba, "500"]] if nba is not None else []}})
    for slug, sn in snaps.items():
        ts0 = int(slug.rsplit("-", 1)[1])
        u = resample(sorted(mids[ts0]))
        if u is None:
            continue
        w = load_window(slug)
        if not w or not w[0]:
            continue
        tape, winner, _ = w
        sn.sort(key=lambda x: x["ts"])
        rec = top_book_window(sn, tape, winner, slug, gate_sec=45.0)
        rows.append((detect_trade(u), regime(u), rec["pair_cost"], rec["pnl"]))


def main():
    rows = []
    for path in sys.argv[1:]:
        process_file(path, rows)
        print("  ...%s -> %d windows so far" % (path.split("/")[-1], len(rows)), file=sys.stderr)
    n = len(rows)

    def stats(sel):
        s = [r for r in rows if sel(r)]
        pnl = [r[3] for r in s]
        pair = [r[2] for r in s if r[2] is not None]
        return len(s), (st.mean(pair) if pair else 0), (st.mean(pnl) if pnl else 0), sum(pnl)

    print("\nwindows: %d\n" % n)
    print("ALL windows (no gate):    n=%d pair %.3f PnL/w $%+.3f total $%+.1f" % stats(lambda r: True))
    print("GATE=TRADE (chop-likely): n=%d pair %.3f PnL/w $%+.3f total $%+.1f" % stats(lambda r: r[0]))
    print("GATE=SKIP  (trend-likely): n=%d pair %.3f PnL/w $%+.3f total $%+.1f  <- avoided" % stats(lambda r: not r[0]))
    print("\ndetector confusion (rows = actual, cols = gate):")
    print("  %-9s | TRADE | SKIP | recall" % "actual")
    for reg in ("chop", "reversal", "trend"):
        tr = sum(1 for r in rows if r[1] == reg and r[0])
        sk = sum(1 for r in rows if r[1] == reg and not r[0])
        tot = tr + sk
        goal = "kept" if reg != "trend" else "skipped"
        rec = (tr if reg != "trend" else sk) / tot * 100 if tot else 0
        print("  %-9s | %5d | %4d | %.0f%% %s" % (reg, tr, sk, rec, goal))


if __name__ == "__main__":
    main()

"""Go/no-go report (scale-free): the merge-maker tactic is SCALE-INVARIANT in
percentage terms, so what matters is the competitor's REALIZED EDGE% of deployed
capital, NOT matching his absolute fill volume. We compute his real edge% directly
from his real subgraph fills + the window winner (no fill model needed), then project
that edge% onto OUR small capital. The fill model is used ONLY to check whether OUR
execution can reproduce his fill PRICES. Read-only, no live trading.
Usage: python3 scripts/_mm_pnl.py [addr]"""
import sys
import statistics
from datetime import datetime, timezone

from quoter.research.mm_tape import load_window, subgraph_targets, ticks_from_tape
from quoter.research.mm_policy import deep_ladder_quotes
from quoter.research.mm_sim import simulate_window
from quoter.research.mm_calibrate import calibrate, realized_pnl
from quoter.research.mm_types import Theta

_EPS = 1e-6

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
STEP = 20                          # quote-refresh cadence (sec)
PRICE_LOSS_GATE = 0.02             # per-window price-loss above this => can't reproduce his prices
WINDOWS_PER_DAY = 288              # 5m windows in a day
CAPITAL_PER_WINDOW = (50, 100, 200)  # $ deployed per 5m window (recycles each window)


def _slug_ts(slug):
    try:
        return int(slug.rsplit("-", 1)[1])
    except (ValueError, IndexError):
        return None


# ── 1. pull a LOT of competitor history for a robust edge estimate ──
tgts = subgraph_targets(ADDR, max_pages=20)
print("subgraph windows found: %d" % len(tgts))

# Resolve the winner for every window (edge% needs only target+winner, NOT the tape).
# Keep the tape too where available (only the price-reproduction check needs it).
edge_rows = []       # {slug, target, winner, spent, pnl, edge}
cal = []             # (tape, winner, ticks) — only windows with a usable tape
cal_targets = []     # aligned targets for the price-reproduction calibration
for t in tgts:
    w = load_window(t["slug"])
    if not w:
        continue
    tape, winner, open_ts = w
    if winner is None:
        continue
    spent = float(t["size_up"]) * float(t["avg_up"]) + float(t["size_dn"]) * float(t["avg_dn"])
    if spent <= _EPS:
        continue
    pnl = realized_pnl(t, winner)
    edge_rows.append({"slug": t["slug"], "winner": winner, "spent": spent,
                      "pnl": pnl, "edge": pnl / spent})
    if tape:  # tape present => usable for the price-reproduction check
        ticks = ticks_from_tape(tape, open_ts, STEP)
        cal.append((tape, winner, ticks))
        cal_targets.append(t)

print("windows with resolved winner (edge%% computable): %d" % len(edge_rows))
print("windows with loadable tape (price-check usable): %d" % len(cal))
if not edge_rows:
    print("no windows with a resolved winner — cannot compute edge%. STOP.")
    sys.exit()

# date span
tss = [ts for ts in (_slug_ts(r["slug"]) for r in edge_rows) if ts]
span = ""
if tss:
    lo = datetime.fromtimestamp(min(tss), timezone.utc).strftime("%Y-%m-%d %H:%M")
    hi = datetime.fromtimestamp(max(tss), timezone.utc).strftime("%Y-%m-%d %H:%M")
    span = "%s .. %s UTC" % (lo, hi)

# ── 2. price-reproduction check (fill model): does OUR execution hit his prices? ──
price_verdict = None
theta = None
price_per_window = None
if cal:
    guru_pol = lambda mid, inv: deep_ladder_quotes(mid, size=200, levels=25, step=0.02)
    grid = [Theta(fill=f, lag=lag)
            for f in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0)
            for lag in (0.0, 2.0, 5.0)]
    theta, _loss, _per = calibrate(cal, cal_targets, guru_pol, grid)
    # price-loss ONLY (size mismatch is expected & irrelevant to edge% — scale-invariant)
    price_loss = 0.0
    for (tape, winner, ticks), tgt in zip(cal, cal_targets):
        r = simulate_window(tape, winner, guru_pol, theta, ticks)
        for got, want in ((r.avg_up, tgt["avg_up"]), (r.avg_dn, tgt["avg_dn"])):
            price_loss += (got - float(want)) ** 2
    price_per_window = price_loss / len(cal)
    price_verdict = price_per_window <= PRICE_LOSS_GATE

# ── aggregate competitor ground-truth edge% ──
edges = [r["edge"] for r in edge_rows]
total_spent = sum(r["spent"] for r in edge_rows)
total_pnl = sum(r["pnl"] for r in edge_rows)
total_edge = total_pnl / total_spent if total_spent else 0.0
mean_edge = statistics.mean(edges)
median_edge = statistics.median(edges)
win_rate = sum(1 for r in edge_rows if r["pnl"] > 0) / len(edge_rows)
stdev_edge = statistics.pstdev(edges) if len(edges) > 1 else 0.0

# ============================ REPORT ============================
print("\n" + "=" * 64)
print("SCALE-FREE GO/NO-GO REPORT  —  competitor %s" % ADDR)
print("=" * 64)

# (a) sample
print("\n(a) SAMPLE")
print("    windows analyzed (edge%%): %d" % len(edge_rows))
print("    date span: %s" % (span or "n/a"))

# (b) competitor ground-truth edge% (no model)
print("\n(b) COMPETITOR GROUND-TRUTH EDGE%  (from real fills + winner, NO model)")
print("    total edge%%  (sum pnl / sum spent): %+.2f%%" % (100 * total_edge))
print("    mean per-window edge%%:              %+.2f%%" % (100 * mean_edge))
print("    median per-window edge%%:            %+.2f%%" % (100 * median_edge))
print("    win-rate (windows with pnl>0):      %.1f%%" % (100 * win_rate))
print("    stdev of per-window edge%% (regime): %.2f%%" % (100 * stdev_edge))
print("    aggregate: spent $%.2f  pnl $%+.2f" % (total_spent, total_pnl))

# (c) price-reproduction verdict
print("\n(c) PRICE-REPRODUCTION VERDICT  (can OUR execution hit his fill prices?)")
if price_verdict is None:
    print("    NO TAPE available for any window — cannot run the price check.")
    print("    (edge%% above still valid; it needs only target+winner.)")
else:
    print("    best theta: fill=%.2f lag=%.1f   price-loss/window=%.4f (gate %.4f)"
          % (theta.fill, theta.lag, price_per_window, PRICE_LOSS_GATE))
    if price_verdict:
        print("    PASS: prices ARE reproducible (avg abs price error ~%.3f)."
              % (price_per_window ** 0.5))
    else:
        print("    WARN: prices NOT reproducible (avg abs price error ~%.3f)."
              % (price_per_window ** 0.5))
        print("    We may not achieve his fill PRICES at our queue position.")
    print("    (Size mismatch is EXPECTED and irrelevant — tactic is scale-invariant.)")

# (d) $/day projection at OUR capital (turnover-based; capital recycles each window)
print("\n(d) $/DAY PROJECTION AT OUR CAPITAL  (turnover: capital recycles each 5m window)")
print("    formula: $/day = capital_per_window * edge%% * %d windows/day" % WINDOWS_PER_DAY)
print("    %-18s %-14s %-14s %-14s" % ("capital/window", "@total-edge", "@mean-edge", "@median-edge"))
for cap in CAPITAL_PER_WINDOW:
    print("    $%-17d $%-13.2f $%-13.2f $%-13.2f"
          % (cap,
             cap * total_edge * WINDOWS_PER_DAY,
             cap * mean_edge * WINDOWS_PER_DAY,
             cap * median_edge * WINDOWS_PER_DAY))

# (e) honest caveats
print("\n(e) HONEST CAVEATS")
print("    - Small sample (~%d windows): may reflect ONE market regime, not a stable edge."
      % len(edge_rows))
print("    - Edge is WINNER-DEPENDENT: per-window pnl swings hard on which side wins")
print("      (stdev %.2f%% vs mean %.2f%%) — high variance, not a smooth yield."
      % (100 * stdev_edge, 100 * mean_edge))
print("    - Subgraph fills may UNDERCOUNT his true volume (missed pages / non-maker legs);")
print("      edge% is scale-free so this is less fatal, but spent/pnl absolutes are lower bounds.")
print("    - Projection assumes we achieve similar FILL SELECTION at small size. His edge")
print("      comes from catching the cheap deep tail on panic dumps — that requires QUEUE")
print("      PRESENCE (resting deep bids that actually get hit). That is the real open risk.")
print("    - Projection assumes BOTH sides actually fill. If only the expensive side fills,")
print("      we hold a NAKED directional position, not a hedged merge — very different risk.")
print("=" * 64)

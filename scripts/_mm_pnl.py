"""Go/no-go report: calibrate the MM sim to competitor 0xb27b, then project our expected
$/day at our capital. Read-only, no live trading. Usage: python3 scripts/_mm_pnl.py [addr]"""
import sys
import statistics
from functools import partial

from quoter.research.mm_tape import load_window, subgraph_targets, ticks_from_tape
from quoter.research.mm_policy import guru_like_quotes, our_quotes, deep_ladder_quotes
from quoter.research.mm_sim import simulate_window
from quoter.research.mm_calibrate import calibrate
from quoter.research.mm_types import Theta

_EPS = 1e-6

ADDR = sys.argv[1] if len(sys.argv) > 1 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
STEP = 20                         # quote-refresh cadence (sec)
LOSS_GATE = 0.5                   # per-window loss above this => projection unreliable
WINDOWS_PER_DAY = 288            # 5m windows in a day

# ── 1. calibration set: competitor real maker fills over RESOLVED 5m windows ──
tgts = subgraph_targets(ADDR)
print("subgraph windows found: %d" % len(tgts))
cal = []
targets = []
for t in tgts:
    w = load_window(t["slug"])
    if not w or not w[0]:
        continue
    tape, winner, open_ts = w
    ticks = ticks_from_tape(tape, open_ts, STEP)
    cal.append((tape, winner, ticks))
    targets.append(t)
print("windows with loadable resolved tapes: %d" % len(cal))
if not cal:
    print("no calibration windows (no resolved tapes for subgraph fills)"); sys.exit()

# ── 2. calibrate deep-ladder policy at competitor SCALE and DEPTH ──
# The competitor rests bids DEEP below mid across the whole curve at large size,
# catching the cheap tail on panic dumps. Match that depth/scale so the policy can
# represent his real (below-VWAP) average fill price.
guru_pol = lambda mid, inv: deep_ladder_quotes(mid, size=200, levels=25, step=0.02)
grid = [Theta(fill=f, lag=lag)
        for f in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0)
        for lag in (0.0, 2.0, 5.0)]
theta, loss, per = calibrate(cal, targets, guru_pol, grid)
per_window = loss / len(cal)
print("best theta: fill=%.2f lag=%.1f  loss/window=%.3f" % (theta.fill, theta.lag, per_window))

# ── 2b. per-term loss breakdown at the calibrated theta (what drives residual) ──
size_loss = 0.0
price_loss = 0.0
for (tape, winner, ticks), tgt in zip(cal, targets):
    r = simulate_window(tape, winner, guru_pol, theta, ticks)
    for got, want in ((r.gross_up, tgt["size_up"]), (r.gross_dn, tgt["size_dn"])):
        denom = abs(float(want)) + _EPS
        size_loss += ((got - float(want)) / denom) ** 2
    for got, want in ((r.avg_up, tgt["avg_up"]), (r.avg_dn, tgt["avg_dn"])):
        price_loss += (got - float(want)) ** 2
print("loss breakdown: size-loss=%.3f  price-loss=%.3f  (total=%.3f, /window size=%.3f price=%.3f)"
      % (size_loss, price_loss, size_loss + price_loss,
         size_loss / len(cal), price_loss / len(cal)))

# ── 3. honesty gate ──
if per_window > LOSS_GATE:
    print("\nHONESTY GATE: sim cannot reproduce the competitor within tolerance")
    print("(loss/window %.3f > %.3f). Projection is UNRELIABLE — do NOT trust a $/day number."
          % (per_window, LOSS_GATE))
    sys.exit()

# ── 4. project OUR policy at OUR capital over the same tapes ──
def run_ours(size, levels, spread):
    pnls = []
    for tape, winner, ticks in cal:
        pol = lambda mid, inv, s=size, l=levels, sp=spread: our_quotes(mid, s, l, sp, inv)
        r = simulate_window(tape, winner, pol, theta, ticks)
        pnls.append(r.pnl)
    return pnls

print("\n=== OUR policy projection (calibrated theta) ===")
base = run_ours(size=5, levels=2, spread=0.02)
avg = statistics.mean(base)
print("per-window PnL: mean $%.4f  median $%.4f  n=%d" % (avg, statistics.median(base), len(base)))
print("=> expected $/day (%d win): $%.2f" % (WINDOWS_PER_DAY, avg * WINDOWS_PER_DAY))
if len(base) > 1 and statistics.pstdev(base) > 0:
    print("   Sharpe-ish (per-window): %.3f" % (avg / statistics.pstdev(base)))

# ── 5. scaling curve ──
print("\n=== scaling curve ($/day) ===")
for size in (5, 10, 25, 50):
    p = run_ours(size=size, levels=2, spread=0.02)
    print("  size %2d: $/day $%.2f  (mean/win $%.4f)" % (size, statistics.mean(p) * WINDOWS_PER_DAY, statistics.mean(p)))

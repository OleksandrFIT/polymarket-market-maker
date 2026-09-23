"""COMBINED-STRATEGY offline backtest (Крок-1 momentum-tilt) over cached 15m windows.

Walks each window minute-by-minute and simulates ALL THREE components together so we
get an end-to-end PnL estimate of the INTEGRATED strategy (not just the tilt signal):

  - BASE maker ladder: rest bids both sides near the entry mid; a rung fills (maker)
    the first minute that side's price drops to it. Imbalance bounded by naked_cap,
    spend bounded by per_window_cap. The crashing loser leg caught here = insurance.
  - TILT: at the decision minute, if |BTC move| confirms a side and the circuit-breaker
    is enabled, taker-buy the favorite (ask = mid + fee) toward favorite_shares ≈ spent,
    capped by step / budget / tilt_max_price.
  - tilt-aware COMPLETION: each minute, if the LOSER is heavy and the pair completes
    < $1, taker-buy the favorite to balance (merge edge); if the FAVORITE is heavy
    (a deliberate tilt) → let it ride (don't pair it off).
  - CB: paper-EV RegimeTracker (the real class) gates the tilt; shadow-tracked.

Resolution: winner = up_mid at the last minute >= 0.5. PnL = winner_shares - spent.

FILL-MODEL CAVEATS (honest): 1-min mid granularity, no order-book depth/queue, no
partial fills, taker fee modeled as a flat `tilt_fee`. This is a DIRECTIONAL ESTIMATE
of combined PnL, not a guarantee. Read-only; imports the real RegimeTracker + detector.
"""
import sys, os, json, time, statistics, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining
from quoter.runner.regime_tracker import RegimeTracker

UA = {"User-Agent": "Mozilla/5.0"}
DIRS = ["/tmp/poly_path15_cache", "/tmp/poly_path15_oos"]
STEP = 900
ENTRY_MIN = 2          # read entry mid after the first noisy minute or two
DECISION_LO, DECISION_HI = 10, 13


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.5)


# ---- load windows + binance ----
byts = {}
for d in DIRS:
    if not os.path.isdir(d):
        continue
    for fn in os.listdir(d):
        try:
            w = json.load(open(os.path.join(d, fn)))
        except Exception:
            w = None
        if w and w.get("up") and w.get("winner") and w.get("ts"):
            byts[w["ts"]] = w
wins = sorted(byts.values(), key=lambda w: w["ts"])
print("pooled %d windows" % len(wins))
clusters, cur = [], [wins[0]]
for w in wins[1:]:
    if w["ts"] - cur[-1]["ts"] > 7200:
        clusters.append(cur); cur = [w]
    else:
        cur.append(w)
clusters.append(cur)
closes = {}
for c in clusters:
    end = (c[-1]["ts"] + STEP) * 1000; lo = c[0]["ts"] - 720
    while True:
        kl = get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&endTime=%d" % end)
        if not kl:
            break
        for k in kl:
            closes[k[0] // 1000] = float(k[4])
        end = kl[0][0] - 60000
        if kl[0][0] // 1000 <= lo:
            break
        time.sleep(0.05)


def btc(t):
    for d in (0, 60, 120):
        if (t - d) in closes:
            return closes[t - d]
    return None


# ---- strategy params ----
# REBALANCED to directional-first (competitor profile ~70% favorite / 30% insurance).
# Tilt sizes to a $ FRACTION of the window cap (scale-correct), NOT favorite≈spent
# (which collapses at small budgets: spent$ ≈ inv_fav shares → gap≈0 → no tilt).
# Base is shrunk so it reserves budget for the tilt instead of eating it first.
cfg = Config(trend_confidence=0.35, trend_gate_sec=600.0)
RUNG_SIZE = 5
RUNGS = 1               # smaller base (was 2) — reserve budget for the directional tilt
SPACING = 0.03
DELTA = cfg.merge_edge / 2          # 0.01 per leg at merge_edge 0.02
MIN_BUY = 0.20
NAKED_CAP = 3           # smaller insurance leg (was 5) — bounds base $ footprint
PER_WINDOW_CAP = 15.0
TILT_FRAC = 0.65        # target favorite $ = TILT_FRAC * per_window_cap (directional-first)
COMPLETE_STEP = 10      # max shares per completion shot
TILT_FEE = 0.02
TILT_MAX = 0.90
MOVE_THR = 20


def sim_window(w, rt):
    up = w["up"]
    e = up[ENTRY_MIN]
    if not (0.35 <= e <= 0.65):
        return None
    # base rung price levels per side (anchored at entry mid)
    yes_lvls = [round(e - DELTA - i * SPACING, 4) for i in range(RUNGS)]
    no_lvls = [round((1 - e) - DELTA - i * SPACING, 4) for i in range(RUNGS)]
    yes_lvls = [L for L in yes_lvls if L >= MIN_BUY]
    no_lvls = [L for L in no_lvls if L >= MIN_BUY]
    yes_done = [False] * len(yes_lvls)
    no_done = [False] * len(no_lvls)

    inv = {"Up": 0.0, "Dn": 0.0}
    cost = {"Up": 0.0, "Dn": 0.0}
    spent = 0.0

    # decide tbias once at decision window (use DECISION_LO)
    strike = btc(w["ts"]); now = btc(w["ts"] + DECISION_LO * 60)
    tbias = "NEUTRAL"; fav_entry_shadow = 0.0; fav = None
    if strike is not None and now is not None and abs(now - strike) > MOVE_THR:
        buf = [(btc(w["ts"] + k * 60), float(w["ts"] + k * 60))
               for k in range(max(0, DECISION_LO - 8), DECISION_LO + 1) if btc(w["ts"] + k * 60) is not None]
        sig = sigma_remaining(buf, 900 - DECISION_LO * 60, cfg)
        b = detect_bias(now, strike, sig, 900 - DECISION_LO * 60, cfg)
        if b != "NEUTRAL":
            tbias = b; fav = "Up" if b == "UP" else "Dn"
            fav_entry_shadow = (up[DECISION_LO] if fav == "Up" else 1 - up[DECISION_LO])

    cb_on = rt.directional_enabled()
    tilt_pnl_basis = 0.0    # cost of tilt shares (to attribute later)
    tilt_shares = 0.0

    def buy(side, price, qty):
        nonlocal spent
        if qty <= 0 or price <= 0:
            return 0.0
        if spent + qty * price > PER_WINDOW_CAP + 1e-9:
            qty = max(0.0, (PER_WINDOW_CAP - spent) / price)
            qty = float(int(qty))
            if qty <= 0:
                return 0.0
        inv[side] += qty; cost[side] += qty * price; spent += qty * price
        return qty

    for m in range(15):
        yp, npr = up[m], 1 - up[m]
        # BASE maker fills: a rung fills the first minute price touches <= level,
        # respecting naked_cap (don't let the heavier side exceed by >cap).
        for i, L in enumerate(yes_lvls):
            if not yes_done[i] and yp <= L and (inv["Up"] - inv["Dn"]) < NAKED_CAP:
                if buy("Up", L, RUNG_SIZE) > 0:
                    yes_done[i] = True
        for i, L in enumerate(no_lvls):
            if not no_done[i] and npr <= L and (inv["Dn"] - inv["Up"]) < NAKED_CAP:
                if buy("Dn", npr if npr <= L else L, RUNG_SIZE) > 0:
                    no_done[i] = True

        # TILT (taker) within decision window, gated by CB + cutoff (cutoff 45s ~ min13).
        # Size to a $ target = TILT_FRAC * cap (scale-correct, directional-first).
        if (fav is not None and cb_on and DECISION_LO <= m <= DECISION_HI):
            fav_ask = (yp if fav == "Up" else npr) + TILT_FEE
            if fav_ask <= TILT_MAX:
                target_usd = TILT_FRAC * PER_WINDOW_CAP
                gap_usd = target_usd - cost[fav]
                if gap_usd > 0:
                    q = gap_usd / fav_ask
                    got = buy(fav, fav_ask, float(int(q)))
                    tilt_shares += got; tilt_pnl_basis += got * fav_ask

        # tilt-aware COMPLETION: if LOSER heavy and pair<$1, buy favorite to balance.
        naked = inv["Up"] - inv["Dn"]
        heavy = "Up" if naked > 0 else ("Dn" if naked < 0 else None)
        if heavy is not None and m >= 3:
            light = "Dn" if heavy == "Up" else "Up"
            fav_side = fav
            # skip if the favorite (tilt) is the heavy side — let it ride
            if not (fav_side is not None and heavy == fav_side):
                heavy_avg = cost[heavy] / inv[heavy] if inv[heavy] else 0
                light_ask = (yp if light == "Up" else npr) + TILT_FEE
                if heavy_avg + light_ask < 1.0:
                    qbal = min(abs(naked), float(COMPLETE_STEP))
                    buy(light, light_ask, float(int(qbal)))

    winner = "Up" if up[14] >= 0.5 else "Dn"
    payout = inv[winner]
    pnl = payout - spent
    # record into CB (shadow) for regime tracking
    if tbias != "NEUTRAL":
        rt.record("Up" if tbias == "UP" else "Down", fav_entry_shadow,
                  "Up" if winner == "Up" else "Down")
    return {"pnl": pnl, "spent": spent, "tilt_shares": tilt_shares,
            "tilt_usd": tilt_pnl_basis, "fired_tilt": tilt_shares > 0, "winner": winner,
            "had_signal": fav is not None, "cb_on": cb_on,
            "inv_up": inv["Up"], "inv_dn": inv["Dn"]}


rt = RegimeTracker(cfg.regime_window, cfg.regime_min_samples, cfg.regime_min_ev, cfg.tilt_fee)
res = [r for r in (sim_window(w, rt) for w in wins) if r]
n = len(res)
tot_pnl = sum(r["pnl"] for r in res)
tot_spent = sum(r["spent"] for r in res)
wins_n = sum(1 for r in res if r["pnl"] > 0.01)
fired = sum(1 for r in res if r["fired_tilt"])
span_days = sum((c[-1]["ts"] - c[0]["ts"]) / 86400 for c in clusters)
per_day = n / span_days if span_days else 0

print("\n" + "=" * 60)
print("  COMBINED STRATEGY SIM (base + tilt + insurance + CB)")
print("=" * 60)
had_sig = sum(1 for r in res if r["had_signal"])
cb_on_n = sum(1 for r in res if r["cb_on"])
tilt_usd = sum(r["tilt_usd"] for r in res)
print("windows traded   : %d (entry mid 0.35-0.65)" % n)
print("  with signal     : %d (%.0f%%) | cb enabled: %d (%.0f%%)" %
      (had_sig, 100 * had_sig / n if n else 0, cb_on_n, 100 * cb_on_n / n if n else 0))
print("tilt fired in     : %d windows (%.0f%%) | tilt $ deployed: $%.0f (%.0f%% of spend)" %
      (fired, 100 * fired / n if n else 0, tilt_usd, 100 * tilt_usd / tot_spent if tot_spent else 0))
print("NET PnL           : $%+.1f over %d windows" % (tot_pnl, n))
print("avg PnL / window  : $%+.3f" % (tot_pnl / n if n else 0))
print("PnL %% of spend    : %+.2f%%" % (100 * tot_pnl / tot_spent if tot_spent else 0))
print("avg spend / window: $%.2f (cap $%.0f)" % (tot_spent / n if n else 0, PER_WINDOW_CAP))
print("win windows       : %d (%.0f%%)" % (wins_n, 100 * wins_n / n if n else 0))
print("windows / day     : %.0f  -> projected NET ~$%+.1f/day" %
      (per_day, (tot_pnl / n) * per_day if n else 0))
worst = min(r["pnl"] for r in res); best = max(r["pnl"] for r in res)
print("best / worst window: $%+.1f / $%+.1f" % (best, worst))
std = statistics.pstdev([r["pnl"] for r in res]) if n > 1 else 0
print("PnL stdev / window : $%.2f  (Sharpe-ish %.2f)" %
      (std, (tot_pnl / n) / std if std else 0))
print("\nNOTE: 1-min path fills, no book depth/queue — directional estimate, not a guarantee.")

"""Honest fidelity check: does the 5m early-consistent-leader edge survive WITHOUT
lookahead? The published +2.78% (_oos_validate) skips rejected windows entirely but
accumulates from minute 0 — a decision that needs minute-1/2 data. That is lookahead.
Live you must buy at min0 before knowing selection. Compare:
  A. published (lookahead): skip rejected, accumulate min 0,1,2 on selected
  B. live-as-implemented: accumulate min0,1 on EVERY window (lean into that min's
     leader); at min2 if not passed STOP (hold carry), else add min2. Blended over ALL.
  C. realistic no-lookahead: enter ONLY at min2 (after filter), at min2 prices, hold.
Read-only, reuses /tmp/poly_path5_cache.
"""
import os, json, statistics

PCACHE = "/tmp/poly_path5_cache"
LEAN = 3
BAND = (0.62, 0.78)

wins = []
for fn in os.listdir(PCACHE):
    try:
        d = json.load(open(os.path.join(PCACHE, fn)))
    except Exception:
        d = None
    if d and d.get("up") and d.get("winner") and d.get("ts") and len(d["up"]) > 2:
        wins.append(d)
wins.sort(key=lambda w: w["ts"])
N = len(wins)
print("cached 5m windows: %d over %.1f days\n" % (N, (wins[-1]["ts"] - wins[0]["ts"]) / 86400))


def leader_at(w, m):
    p = w["up"][m]
    return ("Up" if p > 0.5 else "Dn"), (p if p > 0.5 else 1 - p)


def passed(w):
    l1, _ = leader_at(w, 1)
    l2, p2 = leader_at(w, 2)
    return l1 == l2 and BAND[0] <= p2 <= BAND[1]


def winner(w):
    return "Up" if w["winner"] == "Up" else "Dn"


def policy_A():  # published, lookahead
    pnl = spent = 0.0; n = 0
    for w in wins:
        if not passed(w):
            continue
        up = w["up"]; inv = {"Up": 0.0, "Dn": 0.0}; cost = 0.0
        for m in (0, 1, 2):
            p = up[m]; ld = "Up" if p > 0.5 else "Dn"; lp = p if ld == "Up" else 1 - p
            lag = "Dn" if ld == "Up" else "Up"
            inv[ld] += LEAN; cost += LEAN * lp
            inv[lag] += 1; cost += 1 - lp
        pnl += inv[winner(w)] - cost; spent += cost; n += 1
    return n, pnl, spent


def policy_B():  # live: accumulate on every window, filter only stops further posting
    pnl = spent = 0.0; n_all = 0; n_pass = 0
    for w in wins:
        up = w["up"]; inv = {"Up": 0.0, "Dn": 0.0}; cost = 0.0
        mins = (0, 1, 2) if passed(w) else (0, 1)   # not-passed: stop at min2, hold carry
        for m in mins:
            p = up[m]; ld = "Up" if p > 0.5 else "Dn"; lp = p if ld == "Up" else 1 - p
            lag = "Dn" if ld == "Up" else "Up"
            inv[ld] += LEAN; cost += LEAN * lp
            inv[lag] += 1; cost += 1 - lp
        pnl += inv[winner(w)] - cost; spent += cost; n_all += 1
        if passed(w): n_pass += 1
    return n_all, n_pass, pnl, spent


def policy_C(fee=0.0):  # realistic: enter ONLY at min2 (post-filter), taker, hold
    pnl = spent = 0.0; n = 0
    for w in wins:
        if not passed(w):
            continue
        p = w["up"][2]; ld = "Up" if p > 0.5 else "Dn"; lp = (p if ld == "Up" else 1 - p) + fee
        lag = "Dn" if ld == "Up" else "Up"; gp = (1 - (p if ld == "Up" else 1 - p)) + fee
        inv = {"Up": 0.0, "Dn": 0.0}; cost = 0.0
        inv[ld] += LEAN; cost += LEAN * lp
        inv[lag] += 1; cost += gp
        pnl += inv[winner(w)] - cost; spent += cost; n += 1
    return n, pnl, spent


nA, pA, sA = policy_A()
nB, npB, pB, sB = policy_B()
nC, pC, sC = policy_C()

print("=== policy comparison (lean 3:1, maker fill-at-mid, 0 fee) ===")
print(" A published (lookahead): %d win (%.0f%%)  %+.2f%% of spend  %+.4f/win" %
      (nA, 100 * nA / N, 100 * pA / sA, pA / nA))
print(" B live accumulate-all  : %d win (100%%, %d pass)  %+.2f%% of spend  %+.4f/win" %
      (nB, npB, 100 * pB / sB, pB / nB))
print(" C realistic enter@min2 : %d win (%.0f%%)  %+.2f%% of spend  %+.4f/win" %
      (nC, 100 * nC / N, 100 * pC / sC, pC / nC))
print("\nKey: A needs lookahead (not implementable). B is what the bot does now.")
print("C is the honest implementable edge (enter after the filter fires).")

print("\n=== policy C fee/spread sensitivity (taker at min2) ===")
for fee in (0.0, 0.005, 0.01, 0.015, 0.02):
    nc, pc, sc = policy_C(fee)
    print("  taker cost %.1f cent/share: %+.2f%% of spend  %+.4f/win  %s" %
          (fee * 100, 100 * pc / sc, pc / nc, "+EV" if pc > 0 else "-EV"))

# leader-only variant (drop the lag hedge entirely) at min2, fee sensitivity
def policy_C_leaderonly(fee=0.0):
    pnl = spent = 0.0; n = 0
    for w in wins:
        if not passed(w):
            continue
        p = w["up"][2]; ld = "Up" if p > 0.5 else "Dn"; lp = (p if ld == "Up" else 1 - p) + fee
        inv = {"Up": 0.0, "Dn": 0.0}; cost = 0.0
        inv[ld] += LEAN; cost += LEAN * lp
        pnl += inv[winner(w)] - cost; spent += cost; n += 1
    return n, pnl, spent

print("\n=== leader-only at min2 (no lag hedge) fee sensitivity ===")
for fee in (0.0, 0.005, 0.01, 0.015, 0.02):
    nc, pc, sc = policy_C_leaderonly(fee)
    print("  taker cost %.1f cent/share: %+.2f%% of spend  %+.4f/win  %s" %
          (fee * 100, 100 * pc / sc, pc / nc, "+EV" if pc > 0 else "-EV"))

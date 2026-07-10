# Execution A/B Comparator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the same recorded book tape, compare the two execution styles (top_book maker shadow-fill vs momentum taker) with per-window pair-cost/PnL, so we can see which reaches 0xb27b's pair economics — no risk, offline.

**Architecture:** Pure functions in `quoter/research/exec_ab.py` (`window_record`, `top_book_window`, `momentum_window`) — each takes a window's snapshots + resolved winner and returns a fillquality-shaped record. Thin `scripts/_exec_ab.py` loads the tape, resolves winners via `mm_tape.load_window`, runs both portfolios per window, and prints a side-by-side summary via `pairquality.summarize`. No production-code change.

**Tech Stack:** Python 3, pytest, existing pure helpers (`quoter.research.chase.chase_signal`, `quoter.research.pairquality.summarize`, `quoter.research.mm_tape.load_window`), the shadow-fill rule from `scripts/_momentum.py`.

**Spec:** `docs/superpowers/specs/2026-07-10-execution-ab-comparator-design.md`

---

## File Structure

- Create: `quoter/research/exec_ab.py` — pure `fee`, `_mid`, `_ask`, `window_record`, `top_book_window`, `momentum_window`.
- Create: `scripts/_exec_ab.py` — I/O + main + side-by-side output.
- Test: `tests/test_exec_ab.py` — the three pure functions (deterministic synthetic tapes).

Design note vs spec: the two portfolios live in the pure module (not the script) so they are unit-tested offline; the script is pure glue. This is a testability refinement of the spec's Unit 1/2 — behavior is identical.

---

## Task 1: Pure comparator functions + tests

**Files:**
- Create: `quoter/research/exec_ab.py`
- Test: `tests/test_exec_ab.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_exec_ab.py`:

```python
"""Pure execution-A/B functions: window_record metric + the two per-window portfolios."""
from quoter.research.exec_ab import window_record, top_book_window, momentum_window


def test_window_record_fully_paired():
    r = window_record("top_book", "s", merged=20.0, merged_cost=19.2,
                      inv_up=0.0, inv_dn=0.0, winner="Up", spent=19.2)
    assert r["pair_cost"] == 0.96
    assert r["pairs_merged"] == 20.0
    assert r["naked_resid"] == 0.0
    assert r["resid_outcome"] == "flat"
    assert r["match_naked"] is None
    assert abs(r["pnl"] - (20.0 - 19.2)) < 1e-9        # 20 pairs redeem $1 each, minus spent


def test_window_record_naked_won_and_lost():
    won = window_record("momentum", "s", merged=0.0, merged_cost=0.0,
                        inv_up=5.0, inv_dn=0.0, winner="Up", spent=2.5)
    assert won["pair_cost"] is None
    assert won["naked_resid"] == 5.0
    assert won["match_naked"] == 0.0
    assert won["resid_outcome"] == "WON"
    assert abs(won["pnl"] - (5.0 - 2.5)) < 1e-9         # 5 winning shares redeem $1, minus spent
    lost = window_record("momentum", "s", merged=0.0, merged_cost=0.0,
                         inv_up=5.0, inv_dn=0.0, winner="Down", spent=2.5)
    assert lost["resid_outcome"] == "LOST"
    assert abs(lost["pnl"] - (0.0 - 2.5)) < 1e-9        # loser residual expires worthless


def _snap(ts, ub, ua, db, da):
    return {"ts": ts,
            "yes": {"bids": [[f"{ub:.3f}", "500"]], "asks": [[f"{ua:.3f}", "500"]]},
            "no": {"bids": [[f"{db:.3f}", "500"]], "asks": [[f"{da:.3f}", "500"]]}}


def test_top_book_window_maker_shadow_fill_and_merge():
    # one snapshot mid-window: our Up bid 0.501 & Down bid 0.451; SELL prints at/below each
    # bid fill 5 apiece -> merge 5 -> pair_cost = 0.501 + 0.451 = 0.952, naked 0.
    slug = "btc-updown-5m-1000000000"
    snaps = [_snap(1000000010, 0.50, 0.55, 0.45, 0.50)]
    tape = [{"oi": 0, "side": "SELL", "price": 0.50, "size": 5.0, "ts": 1000000010},
            {"oi": 1, "side": "SELL", "price": 0.45, "size": 5.0, "ts": 1000000010}]
    r = top_book_window(snaps, tape, "Up", slug)
    assert r["style"] == "top_book"
    assert r["pairs_merged"] == 5.0
    assert r["naked_resid"] == 0.0
    assert abs(r["pair_cost"] - 0.952) < 1e-9


def test_momentum_window_taker_chase_pair_over_dollar():
    # rising Up mid (0.50 -> 0.60) fires chase "Up"; taker-buys Up@0.62 + Down@0.42 -> merge 5.
    # pair_cost = 0.62 + 0.42 = 1.04 (over $1: the taker-chase mechanism the sims measured -EV).
    slug = "btc-updown-5m-1000000000"
    snaps = [_snap(1000000000, 0.48, 0.52, 0.48, 0.52),
             _snap(1000000005, 0.58, 0.62, 0.38, 0.42)]
    r = momentum_window(snaps, tape=[], winner="Up", slug=slug)
    assert r["style"] == "momentum"
    assert r["pairs_merged"] == 5.0
    assert abs(r["pair_cost"] - 1.04) < 1e-9
    assert r["naked_resid"] == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_exec_ab.py -q`
Expected: FAIL — `ModuleNotFoundError: quoter.research.exec_ab`.

- [ ] **Step 3: Implement the pure module**

Create `quoter/research/exec_ab.py`:

```python
"""Pure execution-A/B: run the bot's two execution styles over one window's book snapshots and
return a fillquality-shaped record for each. top_book = maker + shadow-fill (OPTIMISTIC — offline
cannot model queue/adverse-selection); momentum = taker-chase (decision-grade, we are aggressor).
window_record computes the shared pair-cost/outcome metric identically for both styles."""
from quoter.research.chase import chase_signal

FEE_RATE = 0.018
TICK = 0.001


def fee(p):
    return FEE_RATE * min(p, 1.0 - p)


def _mid(book):
    bb = max((float(p) for p, _ in book["bids"]), default=None)
    ba = min((float(p) for p, _ in book["asks"]), default=None)
    if bb is None and ba is None:
        return None
    return ba if bb is None else (bb if ba is None else (bb + ba) / 2)


def _ask(book):
    a = book["asks"]
    if not a:
        return None, 0.0
    p, sz = min(((float(p), float(s)) for p, s in a), key=lambda x: x[0])
    return p, sz


def window_record(style, slug, merged, merged_cost, inv_up, inv_dn, winner, spent,
                  completes=0, sells=0):
    """Fillquality-shaped record (shared by both styles). pnl = merged pairs redeem $1 each +
    winning residual redeemed - net spent (loser residual expires; a sell reduces `spent`)."""
    naked = inv_up - inv_dn
    resid_side = "Up" if naked > 0 else ("Down" if naked < 0 else None)
    resid_outcome = ("flat" if resid_side is None
                     else "WON" if resid_side == winner else "LOST")
    redeem = abs(naked) if resid_side == winner else 0.0
    return {
        "style": style, "slug": slug,
        "pair_cost": round(merged_cost / merged, 4) if merged > 0 else None,
        "pairs_merged": merged,
        "naked_resid": round(naked, 1),
        "resid_outcome": resid_outcome,
        "match_naked": round(merged / abs(naked), 1) if abs(naked) >= 1e-9 else None,
        "completes": completes, "sells": sells,
        "spent": round(spent, 2),
        "pnl": round(merged + redeem - spent, 2),
    }


def _merge(inv, held, merged, merged_cost):
    mq = min(inv["Up"], inv["Down"])
    if mq > 0:
        avg_up = held["Up"] / inv["Up"] if inv["Up"] > 0 else 0.0
        avg_dn = held["Down"] / inv["Down"] if inv["Down"] > 0 else 0.0
        merged_cost += mq * (avg_up + avg_dn)
        for s in ("Up", "Down"):
            a = held[s] / inv[s] if inv[s] > 0 else 0.0
            held[s] = max(0.0, held[s] - mq * a)
            inv[s] -= mq
        merged += mq
    return merged, merged_cost


def top_book_window(snaps, tape, winner, slug, cap=6.0, size=5.0, link_margin=0.01,
                    gate_sec=45.0, pwc=15.0, complete_budget=6.0):
    """Maker best+tick both sides, shadow-fill vs SELL prints <= our bid, linked-pair cap on the
    light side, near-end complete (<$1) or sell (>=$1) the naked leg, merge each snapshot."""
    st = {0: [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
          1: [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    oi = {"Up": 0, "Down": 1}
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    spent = merged = merged_cost = 0.0
    completes = sells = 0
    open_ts = int(slug.rsplit("-", 1)[1])
    budget = pwc + complete_budget
    for i, snap in enumerate(snaps):
        ts = snap["ts"]
        end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
        book = {"Up": snap["yes"], "Down": snap["no"]}
        avg = {s: (held[s] / inv[s] if inv[s] > 0 else None) for s in ("Up", "Down")}
        for side in ("Up", "Down"):
            other = "Down" if side == "Up" else "Up"
            b = book[side]
            if not b["bids"] or inv[side] - inv[other] >= cap:
                continue
            bb = max(float(p) for p, _ in b["bids"])
            ba, _sz = _ask(b)
            our = round(bb + TICK, 3)
            if inv[other] > inv[side] and avg[other] is not None:       # linked-pair light cap
                our = min(our, round(1.0 - avg[other] - link_margin, 3))
            if ba is None or our >= ba or our >= 0.99 or our <= 0:
                continue
            v = sum(t["size"] for t in st[oi[side]] if ts <= t["ts"] < end and t["price"] <= our)
            f = min(size, v)
            if f > 0 and spent + f * our <= budget:
                inv[side] += f
                held[side] += f * our
                spent += f * our
        near_end = (open_ts + 300 - ts) <= gate_sec
        naked = inv["Up"] - inv["Down"]
        if near_end and abs(naked) >= 1:
            heavy = "Up" if naked > 0 else "Down"
            light = "Down" if heavy == "Up" else "Up"
            heavy_avg = held[heavy] / inv[heavy] if inv[heavy] > 0 else 0.0
            lap, lsz = _ask(book[light])
            if lap is not None and lap < 0.99 and heavy_avg + lap < 1.0:
                f = min(abs(naked), lsz)
                if f > 0 and spent + f * lap <= budget:
                    inv[light] += f
                    held[light] += f * lap
                    spent += f * lap
                    completes += 1
            else:
                hb = max((float(p) for p, _ in book[heavy]["bids"]), default=None)
                if hb is not None and hb > 0:
                    f = float(int(abs(naked)))
                    if f > 0:
                        avg_h = held[heavy] / inv[heavy] if inv[heavy] > 0 else 0.0
                        held[heavy] = max(0.0, held[heavy] - f * avg_h)
                        inv[heavy] -= f
                        spent -= f * hb            # sell returns cash
                        sells += 1
        merged, merged_cost = _merge(inv, held, merged, merged_cost)
    return window_record("top_book", slug, merged, merged_cost, inv["Up"], inv["Down"],
                         winner, spent, completes, sells)


def momentum_window(snaps, tape, winner, slug, size=5.0, lookback=30, threshold=0.03,
                    resid_cap=8.0, pwc=15.0):
    """Taker-chase: on a momentum signal, take mover + fader at their real asks (incl. taker fee),
    never sell, merge each snapshot. pair_cost uses raw prices; the fee shows up in spent/pnl."""
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    spent = merged = merged_cost = 0.0
    mid_hist = []
    for snap in snaps:
        ts = snap["ts"]
        book = {"Up": snap["yes"], "Down": snap["no"]}
        m = _mid(snap["yes"])
        if m is not None:
            mid_hist.append((ts, m))
        sig = chase_signal(mid_hist, ts, lookback, threshold)
        if sig is not None:
            fade = "Down" if sig == "Up" else "Up"
            for side in (sig, fade):
                other = "Down" if side == "Up" else "Up"
                if inv[side] - inv[other] >= resid_cap:
                    continue
                ap, _asz = _ask(book[side])
                if ap is None or ap >= 0.99 or ap <= 0:
                    continue
                unit = size * (ap + fee(ap))
                if spent + unit > pwc:
                    continue
                inv[side] += size
                held[side] += size * ap          # raw price basis for pair_cost; fee is in spent
                spent += unit
        merged, merged_cost = _merge(inv, held, merged, merged_cost)
    return window_record("momentum", slug, merged, merged_cost, inv["Up"], inv["Down"],
                         winner, spent)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_exec_ab.py -q`
Expected: PASS (4 passed). If a portfolio test fails, fix the implementation (do NOT weaken assertions).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (4 new tests added to the prior count).

- [ ] **Step 6: Commit**

```bash
git add quoter/research/exec_ab.py tests/test_exec_ab.py
git commit -m "feat(research): pure execution-A/B portfolios (maker shadow-fill vs taker chase) + pair-cost record

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Comparator CLI (`scripts/_exec_ab.py`)

**Files:**
- Create: `scripts/_exec_ab.py`

- [ ] **Step 1: Write the script**

Create `scripts/_exec_ab.py`:

```python
"""Execution A/B on the SAME recorded book tape: top_book (maker, shadow-fill, OPTIMISTIC) vs
momentum (taker, decision-grade). Per-window pair_cost/match_naked/PnL for each -> side-by-side.
FIDELITY: momentum = aggressor, real; top_book = shadow-fill UPPER BOUND (offline cannot model
adverse selection). This is a cheap preview of the LIVE topbook_fillquality measurement, NOT a
substitute. Run: POLY_MM_CACHE=/home/ubuntu/cache_poly_mm python3 scripts/_exec_ab.py <book_jsonl>"""
import sys
import json
import collections

from quoter.research.mm_tape import load_window
from quoter.research.pairquality import summarize
from quoter.research.exec_ab import top_book_window, momentum_window

BOOK = sys.argv[1] if len(sys.argv) > 1 else "/tmp/book_all.jsonl"


def _load(path):
    byslug = collections.defaultdict(list)
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        byslug[r["slug"]].append(r)
    for s in byslug.values():
        s.sort(key=lambda x: x["ts"])
    return byslug


def _row(name, recs):
    s = summarize(recs)
    pnl = [r["pnl"] for r in recs]
    return "%-34s pair mean %s med %s | %%<$1 %s | match:naked %s | PnL/win $%+.3f | %s" % (
        name,
        "%.4f" % s["mean_pair_cost"] if s["mean_pair_cost"] is not None else "n/a",
        "%.4f" % s["median_pair_cost"] if s["median_pair_cost"] is not None else "n/a",
        "%3.0f%%" % (100 * s["pct_sub_dollar"]) if s["pct_sub_dollar"] is not None else "n/a",
        "%.1f" % s["median_match_naked"] if s["median_match_naked"] is not None else "n/a",
        (sum(pnl) / len(pnl)) if pnl else 0.0,
        s["outcomes"])


def main():
    byslug = _load(BOOK)
    tb, mo = [], []
    for slug, snaps in byslug.items():
        w = load_window(slug)
        if not w or not w[0]:
            continue
        _tape, winner, _ = w
        tb.append(top_book_window(snaps, _tape, winner, slug))
        mo.append(momentum_window(snaps, _tape, winner, slug))
    if not tb:
        print("no resolved windows in tape (need network/cache to resolve winners via load_window)")
        return
    print("windows: %d (same tape, same resolved winners)\n" % len(tb))
    print("FIDELITY: momentum = taker, decision-grade | top_book = maker, OPTIMISTIC shadow-fill")
    print("          (offline can't model adverse selection; top_book pair_cost is a BEST CASE)\n")
    print(_row("top_book (maker, OPTIMISTIC)", tb))
    print(_row("momentum (taker, real)", mo))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check + no-crash smoke run**

Run: `.venv/bin/python -m py_compile scripts/_exec_ab.py && echo compile-ok`
Expected: `compile-ok`.

Then a no-network smoke run on a 1-window synthetic tape (winners won't resolve offline → the script must exit cleanly with the "no resolved windows" message, proving the load/loop/guard path runs without crashing):

```bash
printf '%s\n' \
 '{"slug":"btc-updown-5m-1000000000","ts":1000000010,"yes":{"bids":[["0.50","500"]],"asks":[["0.55","500"]]},"no":{"bids":[["0.45","500"]],"asks":[["0.50","500"]]}}' \
 > /tmp/_exec_ab_smoke.jsonl
POLY_MM_CACHE=/tmp/_exec_ab_cache .venv/bin/python scripts/_exec_ab.py /tmp/_exec_ab_smoke.jsonl
```
Expected: either the side-by-side table (if `load_window` resolves the winner online) OR the line `no resolved windows in tape ...` — both prove the script runs end-to-end without error. (The real run happens on the server's `data/book_*.jsonl` slice.)

- [ ] **Step 3: Commit**

```bash
git add scripts/_exec_ab.py
git commit -m "feat(research): execution-A/B CLI — side-by-side maker-vs-taker pair economics on one tape

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## After both tasks

The comparator is ready. Running it on a server tape slice (`POLY_MM_CACHE=... python3 scripts/_exec_ab.py data/book_<day>.jsonl`) prints the side-by-side maker-vs-taker pair economics on identical windows — with the fidelity asymmetry stated in the output (momentum real, top_book optimistic). No production code changed. This informs whether the live `topbook_fillquality` measurement is worth running.

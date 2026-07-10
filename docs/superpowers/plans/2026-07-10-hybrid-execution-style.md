# Hybrid Execution Style — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Steps use checkbox syntax.

**Goal:** Add `hybrid_window` (0xb27b's 53/47 maker+taker replica) to the comparator and measure the naked win-rate across all three styles on the same tape.

**Architecture:** New pure `hybrid_window` in `quoter/research/exec_ab.py` (maker shadow-fill both sides + continuous early taker-chase of the rising winner + merge + never sell), wired as a third row in `scripts/_exec_ab.py` and `scripts/_exec_ab_detail.py`. No production change.

**Spec:** `docs/superpowers/specs/2026-07-10-hybrid-execution-style-design.md`

---

## Task 1: `hybrid_window` + tests

**Files:** Modify `quoter/research/exec_ab.py`; add tests to `tests/test_exec_ab.py`.

- [ ] **Step 1: Add the two failing tests** to `tests/test_exec_ab.py` (import `hybrid_window`):

```python
def test_hybrid_window_takes_the_rising_winner():
    # rising Up (0.50->0.60), empty tape -> only the taker leg fires -> accumulate Up (winner)
    # -> naked +5 Up, winner Up -> WON (the fair-coin-winner behaviour, not the loser).
    slug = "btc-updown-5m-1000000000"
    snaps = [_snap(1000000000, 0.48, 0.52, 0.48, 0.52),
             _snap(1000000005, 0.58, 0.62, 0.38, 0.42)]
    r = hybrid_window(snaps, tape=[], winner="Up", slug=slug)
    assert r["style"] == "hybrid"
    assert r["naked_resid"] == 5.0
    assert r["resid_outcome"] == "WON"
    assert r["pairs_merged"] == 0.0


def test_hybrid_window_pairs_maker_loser_with_taker_winner():
    # rising Up + a cheap Down SELL print: taker takes Up (winner), maker catches Down (cheap
    # loser), merge -> a matched pair (the hybrid 53/47 pairing).
    slug = "btc-updown-5m-1000000000"
    snaps = [_snap(1000000000, 0.48, 0.52, 0.48, 0.52),
             _snap(1000000005, 0.58, 0.62, 0.10, 0.14)]
    tape = [{"oi": 1, "side": "SELL", "price": 0.10, "size": 5.0, "ts": 1000000005}]
    r = hybrid_window(snaps, tape, "Up", slug)
    assert r["style"] == "hybrid"
    assert r["pairs_merged"] == 5.0
    assert r["naked_resid"] == 0.0
```

Import line at top: `from quoter.research.exec_ab import window_record, top_book_window, momentum_window, hybrid_window`

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_exec_ab.py -q` → FAIL (ImportError: hybrid_window).

- [ ] **Step 3: Add `hybrid_window`** to `quoter/research/exec_ab.py` (after `momentum_window`):

```python
def hybrid_window(snaps, tape, winner, slug, cap=6.0, size=5.0, lookback=30, threshold=0.03,
                  resid_cap=8.0, pwc=15.0):
    """0xb27b's ~53/47 replica: MAKER bids on both sides catch the cheap FALLER (shadow-fill vs
    SELL prints <= our bid) + TAKER chases the rising WINNER EVERY tick from early (average in at a
    low basis), merge continuously, never sell. Residual leans the winner (fair coin)."""
    st = {0: [t for t in tape if t["oi"] == 0 and t["side"] == "SELL"],
          1: [t for t in tape if t["oi"] == 1 and t["side"] == "SELL"]}
    oi = {"Up": 0, "Down": 1}
    inv = {"Up": 0.0, "Down": 0.0}
    held = {"Up": 0.0, "Down": 0.0}
    spent = merged = merged_cost = 0.0
    mid_hist = []
    for i, snap in enumerate(snaps):
        ts = snap["ts"]
        end = snaps[i + 1]["ts"] if i + 1 < len(snaps) else ts + 2
        book = {"Up": snap["yes"], "Down": snap["no"]}
        m = _mid(snap["yes"])
        if m is not None:
            mid_hist.append((ts, m))
        # MAKER: rest best+tick both sides, shadow-fill vs SELL prints <= our bid (cheap faller)
        for side in ("Up", "Down"):
            other = "Down" if side == "Up" else "Up"
            b = book[side]
            if not b["bids"] or inv[side] - inv[other] >= cap:
                continue
            bb = max(float(p) for p, _ in b["bids"])
            ba, _sz = _ask(b)
            our = round(bb + TICK, 3)
            if ba is None or our >= ba or our >= 0.99 or our <= 0:
                continue
            v = sum(t["size"] for t in st[oi[side]] if ts <= t["ts"] < end and t["price"] <= our)
            f = min(size, v)
            if f > 0 and spent + f * our <= pwc:
                inv[side] += f
                held[side] += f * our
                spent += f * our
        # TAKER: chase the rising winner EVERY tick (continuous early averaging-in)
        sig = chase_signal(mid_hist, ts, lookback, threshold)
        if sig is not None:
            other = "Down" if sig == "Up" else "Up"
            if inv[sig] - inv[other] < resid_cap:
                ap, asz = _ask(book[sig])
                if ap is not None and 0 < ap < 0.99:
                    f = min(size, asz)
                    unit = f * (ap + fee(ap))
                    if f > 0 and spent + unit <= pwc:
                        inv[sig] += f
                        held[sig] += f * ap
                        spent += unit
        merged, merged_cost = _merge(inv, held, merged, merged_cost)
    return window_record("hybrid", slug, merged, merged_cost, inv["Up"], inv["Down"], winner, spent)
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_exec_ab.py -q` → PASS (7 total).
- [ ] **Step 5: Full suite** `.venv/bin/python -m pytest -q` → green.
- [ ] **Step 6: Commit** `feat(research): hybrid_window — 0xb27b 53/47 execution replica for the A/B comparator`.

## Task 2: Wire the third row into both comparator scripts

**Files:** Modify `scripts/_exec_ab.py`, `scripts/_exec_ab_detail.py`.

- [ ] **Step 1:** In `scripts/_exec_ab.py`: import → `from quoter.research.exec_ab import top_book_window, momentum_window, hybrid_window`; in `main`, add `hy = []` and `hy.append(hybrid_window(snaps, _tape, winner, slug))`; add `print(_row("hybrid (0xb27b 53/47 replica)", hy))` after the momentum row.

- [ ] **Step 2:** In `scripts/_exec_ab_detail.py`: same import addition; add `hy.append(hybrid_window(...))` in the loop; add `analyze("hybrid (0xb27b 53/47 replica)", hy)` after the momentum analyze.

- [ ] **Step 3:** Compile check both: `.venv/bin/python -m py_compile scripts/_exec_ab.py scripts/_exec_ab_detail.py && echo ok`.

- [ ] **Step 4: Commit** `feat(research): wire hybrid style as third row in exec-A/B comparator + detail`.

## After both tasks

Deploy the three files to the server and re-run on the same Jul-8 slice → naked win-rate for top_book / momentum / hybrid side by side. Decision: hybrid win-rate ~45-55% → fair coin reproducible; <35% → adverse even with his execution → branch closed.

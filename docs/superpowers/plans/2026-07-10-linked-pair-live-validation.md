# Linked-Pair Live Validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument `_top_book_window` with realized pair-cost telemetry (event `topbook_fillquality`) and a pure aggregator, so a later gated live run can measure whether linked-pair quoting assembles matched pairs at cost < $1.

**Architecture:** Log-only instrumentation on the existing `top_book` merge-maker path (no trading-logic change). One accumulator (`merged_cost`) + two counters + a window-summary event in `quoter/runner/merge_runner.py`. A pure `quoter/research/pairquality.py` (parse + summarize) with a thin `scripts/_pairquality.py` CLI. Full TDD.

**Tech Stack:** Python 3, asyncio, pytest, existing structured logger (`quoter.ops.logger`), existing top_book test harness pattern (`tests/test_top_book_complete.py`).

**Spec:** `docs/superpowers/specs/2026-07-10-linked-pair-live-validation-design.md`

---

## File Structure

- Modify: `quoter/runner/merge_runner.py` — `_top_book_window` (init vars ~line 867; last_mid update ~after line 881; merge accumulation ~line 1059; fillquality emit ~before line 1086; two counter bumps at lines 1027/1050).
- Create: `quoter/research/pairquality.py` — pure `parse_lines` + `summarize`.
- Create: `scripts/_pairquality.py` — thin CLI wrapper.
- Test: `tests/test_topbook_pairquality.py` — window telemetry (2 tests).
- Test: `tests/test_pairquality_aggregator.py` — aggregator (2 tests).

---

## Task 1: Realized pair-cost telemetry in `_top_book_window`

**Files:**
- Modify: `quoter/runner/merge_runner.py`
- Test: `tests/test_topbook_pairquality.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_topbook_pairquality.py`. It reuses the harness pattern from `tests/test_top_book_complete.py` (mock books, vanish-fill for Up, FOK completion for Down) and adds a log-capture so the emitted `topbook_fillquality` event can be asserted.

```python
"""topbook_fillquality telemetry: realized pair_cost of merged pairs + naked residual outcome.
Log-only instrumentation — asserts the emitted measurement event, no behavior change."""
import asyncio

from quoter.config import Config
from quoter.runner import merge_runner
from quoter.runner.merge_runner import MergeRunner
from quoter.runner.trading_state import TradingState


class _Ctl:
    def __init__(self):
        self.t_remaining = 100.0
        self.tick = 0
        self.clock = 1000.0
        self.dt = 0.0
        self.places = []
        self.open = set()
        self.kind = {}
        self.size = {}
        self.seq = 0


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d


def _book(bid, ask):
    return {"bids": [{"price": f"{bid:.3f}", "size": "500"}],
            "asks": [{"price": f"{ask:.3f}", "size": "500"}]}


class _M:
    slug = "btc-updown-5m-x"
    yes_token = "UP"
    no_token = "DN"
    open_ts = 1

    def __init__(self, ctl):
        self._ctl = ctl

    def time_remaining(self):
        return self._ctl.t_remaining


def _make_runner(ctl, fok_fills=True):
    r = MergeRunner.__new__(MergeRunner)
    r.cfg = Config(
        strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=6.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=15.0, tb_complete=True, tb_complete_gate_sec=45.0,
        complete_budget=6.0, inv_reconcile_grace_sec=12.0, dry_run=False,
    )
    r.state = TradingState()
    r._shutdown = False
    r._traded_windows = set()

    async def fake_place(**kw):
        ctl.seq += 1
        oid = f"o{ctl.seq}"
        ctl.kind[oid] = "FOK" if kw.get("order_type") == "FOK" else "maker"
        ctl.size[oid] = kw["size"]
        ctl.places.append((kw["token_id"], kw["side"], kw["price"], kw["size"],
                           kw.get("post_only"), kw.get("order_type")))
        if kw.get("post_only") and kw["token_id"] == "DN":
            ctl.open.add(oid)
        return {"order_id": oid, "status": "live"}

    async def fake_cancel(oids):
        for o in oids:
            ctl.open.discard(o)

    async def fake_cancel_all():
        return 0

    async def fake_merge(m, qty):
        return True

    def order_matched(oid):
        if ctl.kind.get(oid) == "FOK":
            return ctl.size.get(oid, 0.0) if fok_fills else 0.0
        return ctl.size.get(oid, 5.0)

    r._place_limit = fake_place
    r._cancel_orders = fake_cancel
    r._open_order_ids = lambda: set(ctl.open)
    r._order_matched = order_matched
    r._merge_pairs = fake_merge
    r.cancel_all = fake_cancel_all
    return r


def _capture(monkeypatch):
    events = []

    class _L:
        def info(self, ev, **kw):
            events.append((ev, kw))

        def warning(self, ev, **kw):
            pass

    monkeypatch.setattr(merge_runner, "log", _L())
    return events


def _run(ctl, runner, n_ticks, monkeypatch, up=(0.50, 0.99), dn=(0.01, 0.45), t_after=40.0):
    class _Cl:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Resp(_book(*up)) if params["token_id"] == "UP" else _Resp(_book(*dn))

    async def fake_sleep(_):
        ctl.tick += 1
        ctl.clock += ctl.dt
        ctl.t_remaining = t_after
        if ctl.tick >= n_ticks:
            runner._shutdown = True

    monkeypatch.setattr(merge_runner.httpx, "AsyncClient", lambda *a, **k: _Cl())
    monkeypatch.setattr(merge_runner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(merge_runner, "monotonic", lambda: ctl.clock)
    asyncio.run(runner._top_book_window(_M(ctl), 0.5))


def _fq(events):
    fqs = [kw for ev, kw in events if ev == "topbook_fillquality"]
    assert len(fqs) == 1, f"expected one fillquality event, got {len(fqs)}"
    return fqs[0]


def test_fully_paired_pair_cost_equals_spent_over_pairs(monkeypatch):
    # Up fills (vanish) + Down completed (FOK) near-end -> all merged, naked 0.
    # When nothing rides naked, every $ spent went into pairs, so pair_cost == spent / pairs.
    ctl = _Ctl()
    runner = _make_runner(ctl)
    events = _capture(monkeypatch)
    _run(ctl, runner, 2, monkeypatch)
    fq = _fq(events)
    assert fq["pairs_merged"] == 5.0
    assert fq["naked_resid"] == 0.0
    assert fq["resid_outcome"] == "flat"
    assert fq["match_naked"] is None
    assert fq["pair_cost"] is not None
    assert 0.0 < fq["pair_cost"] < 1.0
    assert abs(fq["pair_cost"] - fq["spent"] / fq["pairs_merged"]) < 0.005


def test_naked_residual_outcome_and_none_pair_cost(monkeypatch):
    # completion FOK killed -> Down never fills -> naked +5 Up, nothing merged.
    # Up book (0.50/0.99) -> mid 0.745 >= 0.5 -> winner Up -> naked Up == winner -> WON.
    ctl = _Ctl()
    runner = _make_runner(ctl, fok_fills=False)
    events = _capture(monkeypatch)
    _run(ctl, runner, 2, monkeypatch)
    fq = _fq(events)
    assert fq["pairs_merged"] == 0.0
    assert fq["pair_cost"] is None
    assert fq["naked_resid"] == 5.0
    assert fq["match_naked"] == 0.0
    assert fq["resid_outcome"] == "WON"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_topbook_pairquality.py -q`
Expected: FAIL — `KeyError`/assert on missing `topbook_fillquality` event (the event does not exist yet).

- [ ] **Step 3: Add the accumulator, counters, and last-mid init**

In `quoter/runner/merge_runner.py`, in `_top_book_window`, find (~line 867):

```python
        merged = 0.0
```

Replace with:

```python
        merged = 0.0
        merged_cost = 0.0                  # realized $ cost basis of merged pairs (-> pair_cost)
        completes = 0                      # count of filled completion FOKs
        sells = 0                          # count of filled sell-naked FOKs
        last_mid = mid_at_entry            # last observed Up mid (winner proxy at window end)
```

- [ ] **Step 4: Track last_mid each tick from the Up book**

Find the book-fetch block (the `try/except` that GETs `by`/`bn`, ~lines 876-881), which ends:

```python
                    except Exception:
                        await asyncio.sleep(cadence)
                        continue
```

Immediately AFTER that `except` block (fetch succeeded → `by` is valid), insert:

```python
                    try:                       # winner proxy for the fillquality summary
                        _bb = max((float(x["price"]) for x in by.get("bids", [])), default=None)
                        _ba = min((float(x["price"]) for x in by.get("asks", [])), default=None)
                        if _bb is not None or _ba is not None:
                            last_mid = _ba if _bb is None else (_bb if _ba is None else (_bb + _ba) / 2)
                    except Exception:
                        pass
```

- [ ] **Step 5: Count filled completes and sells**

Find (~line 1027) the completion credit:

```python
                                        log.info("topbook_complete", side=light, qty=filled,
                                                 price=round(light_ask, 3))
```

Add on the next line (same indentation as the `log.info`):

```python
                                        completes += 1
```

Find (~line 1050) the sell credit:

```python
                                        log.info("topbook_sell_naked", side=heavy, qty=sold,
                                                 price=round(heavy_bid, 3))
```

Add on the next line (same indentation):

```python
                                        sells += 1
```

- [ ] **Step 6: Accumulate merged_cost at each merge**

Find the merge apply block (~line 1059):

```python
                            if ok:
                                for s in ("Up", "Down"):
                                    avg_s = held_cost[s] / inv[s] if inv[s] > 0 else 0.0
```

Insert between `if ok:` and `for s in ("Up", "Down"):` (so it reads the PRE-merge held averages):

```python
                            if ok:
                                avg_up = held_cost["Up"] / inv["Up"] if inv["Up"] > 0 else 0.0
                                avg_dn = held_cost["Down"] / inv["Down"] if inv["Down"] > 0 else 0.0
                                merged_cost += mq * (avg_up + avg_dn)
                                for s in ("Up", "Down"):
                                    avg_s = held_cost[s] / inv[s] if inv[s] > 0 else 0.0
```

- [ ] **Step 7: Emit the `topbook_fillquality` window summary**

Find the end-of-window block (~line 1085):

```python
        committed = cost["Up"] + cost["Down"] + sum(p * sz for (p, sz) in resting.values())
        log.info("topbook_done", slug=m.slug, merged=merged,
                 inv_up=inv["Up"], inv_dn=inv["Down"],
                 spent=round(cost["Up"] + cost["Down"], 2), committed=round(committed, 2))
```

Insert BEFORE the `committed = ...` line:

```python
        naked = inv["Up"] - inv["Down"]
        win = "Up" if last_mid >= 0.5 else "Down"
        resid_outcome = ("flat" if abs(naked) < 1e-9
                         else "WON" if ((naked > 0) == (win == "Up")) else "LOST")
        match_naked = (merged / abs(naked)) if abs(naked) >= 1e-9 else None
        log.info("topbook_fillquality", slug=m.slug,
                 pair_cost=round(merged_cost / merged, 4) if merged > 0 else None,
                 pairs_merged=merged,
                 naked_resid=round(naked, 1),
                 resid_outcome=resid_outcome,
                 match_naked=round(match_naked, 1) if match_naked is not None else None,
                 completes=completes, sells=sells,
                 spent=round(cost["Up"] + cost["Down"], 2))
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_topbook_pairquality.py -q`
Expected: PASS (2 passed).

- [ ] **Step 9: Run the full suite (no regression)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the two new tests added to the prior green count).

- [ ] **Step 10: Commit**

```bash
git add quoter/runner/merge_runner.py tests/test_topbook_pairquality.py
git commit -m "feat(topbook): realized pair-cost telemetry (topbook_fillquality) for linked-pair validation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Pair-quality aggregator (pure module + CLI)

**Files:**
- Create: `quoter/research/pairquality.py`
- Create: `scripts/_pairquality.py`
- Test: `tests/test_pairquality_aggregator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pairquality_aggregator.py`:

```python
"""Aggregate topbook_fillquality log records into the decision-grade pair-cost verdict."""
from quoter.research.pairquality import parse_lines, summarize


def _line(pair_cost, naked_resid, resid_outcome, pairs=20.0):
    import json
    return json.dumps({
        "event": "topbook_fillquality", "slug": "btc-updown-5m-1",
        "pair_cost": pair_cost, "pairs_merged": pairs, "naked_resid": naked_resid,
        "resid_outcome": resid_outcome, "match_naked": None, "completes": 1,
        "sells": 0, "spent": 10.0, "level": "info",
    })


def test_parse_ignores_non_fillquality_and_garbage():
    lines = [
        '{"event": "topbook_done", "slug": "x"}',
        "not json at all",
        _line(0.95, 0.0, "flat"),
    ]
    recs = parse_lines(lines)
    assert len(recs) == 1
    assert recs[0]["pair_cost"] == 0.95


def test_summarize_computes_pct_sub_dollar_and_outcomes():
    recs = parse_lines([
        _line(0.95, 0.0, "flat"),
        _line(0.98, 0.0, "flat"),
        _line(1.04, 5.0, "LOST"),
        _line(None, 5.0, "WON"),      # no pairs -> excluded from pair_cost stats
    ])
    s = summarize(recs)
    assert s["n_windows"] == 4
    assert s["n_priced"] == 3                       # windows with a pair_cost
    assert abs(s["mean_pair_cost"] - (0.95 + 0.98 + 1.04) / 3) < 1e-9
    assert abs(s["pct_sub_dollar"] - 2 / 3) < 1e-9  # 0.95, 0.98 < 1.0
    assert s["outcomes"] == {"flat": 2, "LOST": 1, "WON": 1}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pairquality_aggregator.py -q`
Expected: FAIL — `ModuleNotFoundError: quoter.research.pairquality`.

- [ ] **Step 3: Implement the pure module**

Create `quoter/research/pairquality.py`:

```python
"""Pure aggregation of `topbook_fillquality` log records -> pair-cost verdict.
The decision metric: what fraction of windows assembled a matched pair < $1 (linked-pair
holds live) vs >= $1 (async/adverse still beats our execution). No I/O here."""
import json
import statistics as st


def parse_lines(lines):
    """Yield the parsed topbook_fillquality records from an iterable of log lines."""
    out = []
    for line in lines:
        line = line.strip()
        if '"topbook_fillquality"' not in line:
            continue
        try:
            r = json.loads(line)
        except (ValueError, TypeError):
            continue
        if r.get("event") == "topbook_fillquality":
            out.append(r)
    return out


def summarize(records):
    """Distribution + verdict inputs across fillquality records."""
    priced = [r["pair_cost"] for r in records if r.get("pair_cost") is not None]
    outcomes = {}
    for r in records:
        outcomes[r.get("resid_outcome", "?")] = outcomes.get(r.get("resid_outcome", "?"), 0) + 1
    matched = [r["match_naked"] for r in records if r.get("match_naked") is not None]
    return {
        "n_windows": len(records),
        "n_priced": len(priced),
        "mean_pair_cost": (sum(priced) / len(priced)) if priced else None,
        "median_pair_cost": st.median(priced) if priced else None,
        "pct_sub_dollar": (sum(1 for x in priced if x < 1.0) / len(priced)) if priced else None,
        "median_match_naked": st.median(matched) if matched else None,
        "outcomes": outcomes,
        "completes": sum(r.get("completes", 0) for r in records),
        "sells": sum(r.get("sells", 0) for r in records),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pairquality_aggregator.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Add the thin CLI wrapper**

Create `scripts/_pairquality.py`:

```python
"""Read a control.log (arg or stdin), aggregate topbook_fillquality, print the pair-cost verdict.
Usage: python3 scripts/_pairquality.py /path/to/control.log   (or: ... < control.log)"""
import sys

from quoter.research.pairquality import parse_lines, summarize


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            recs = parse_lines(f)
    else:
        recs = parse_lines(sys.stdin)
    s = summarize(recs)
    if not s["n_windows"]:
        print("no topbook_fillquality windows found")
        return
    print("windows: %d (priced %d)" % (s["n_windows"], s["n_priced"]))
    if s["n_priced"]:
        print("pair_cost: mean %.4f | median %.4f | %% windows < $1: %.0f%%" % (
            s["mean_pair_cost"], s["median_pair_cost"], 100 * s["pct_sub_dollar"]))
    print("match:naked median: %s | outcomes: %s | completes %d | sells %d" % (
        ("%.1f" % s["median_match_naked"]) if s["median_match_naked"] is not None else "n/a",
        s["outcomes"], s["completes"], s["sells"]))
    if s["n_priced"]:
        ok = s["mean_pair_cost"] <= 0.99 and s["pct_sub_dollar"] > 0.70
        print("VERDICT: %s (criterion: mean <= 0.99 AND >70%% windows < $1)" % (
            "linked-pair HOLDS live -> scale is the only lever" if ok
            else "pair >= $1 -> async/adverse beats execution; top_book ceiling confirmed"))


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Smoke-test the CLI**

Run: `printf '%s\n' '{"event":"topbook_fillquality","pair_cost":0.96,"pairs_merged":20,"naked_resid":0,"resid_outcome":"flat","match_naked":null,"completes":1,"sells":0,"spent":19.2}' | .venv/bin/python scripts/_pairquality.py`
Expected: prints `windows: 1 (priced 1)`, `pair_cost: mean 0.9600 ...`, and a VERDICT line (here `>70%` fails at n=1 so it prints the ceiling-confirmed branch — that's fine for a smoke test).

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add quoter/research/pairquality.py scripts/_pairquality.py tests/test_pairquality_aggregator.py
git commit -m "feat(research): pair-quality aggregator — % windows with pair < \$1 verdict

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## After both tasks

The bot now emits decision-grade `topbook_fillquality` telemetry and the aggregator turns a
live run's control.log into the pair-cost verdict. NO trading logic changed; default behavior
byte-identical. The gated live run (server-only, explicit "go", watchdog dd-stop, neutral
`REGIME_GATE=0`, size 5) is a SEPARATE operator step — not part of this implementation.

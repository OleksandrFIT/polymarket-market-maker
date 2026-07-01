# 5m early-consistent-leader strategy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Додати окрему 5m «early-consistent-leader maker-lean» стратегію (dry-run + paper-fill оцінка PnL), не чіпаючи наявний 15m-tilt; live лишається замкнено.

**Architecture:** Дві нові чисті функції/класи (`five_min_planner.plan_five_min`, `paper_fill.PaperBook`) + новий метод `merge_runner._five_min_window` (glue), вибір через `cfg.strategy`. Накопичувати перекіс у раннього лідера з відкриття; на хв2 фільтр (лідер послідовний хв1==хв2 + смуга 0.62–0.78); тримати до резолву; оцінити PnL через paper-fill проти живої книги.

**Tech Stack:** Python 3.13, pytest (`.venv/bin/python -m pytest`), наявний CLOB-клієнт (dry-run wrappers), Binance не потрібен.

**Spec:** `docs/superpowers/specs/2026-07-01-five-min-early-leader-design.md`. Гілка `feat/five-min-strategy`. Baseline: 302 passed.

---

## File Structure
- **Create** `quoter/runner/five_min_planner.py` — чиста `plan_five_min` (рішення що постити).
- **Create** `quoter/runner/paper_fill.py` — чистий `PaperBook` (оцінка maker-філлів).
- **Create** `tests/test_five_min_planner.py`, `tests/test_paper_fill.py`.
- **Modify** `quoter/config.py` — поля `strategy`, `lean`, `band_lo`, `band_hi`.
- **Modify** `quoter/runner/merge_runner.py` — метод `_five_min_window` + dispatch.
- **Modify** `quoter/runner/run_control.py` — вибір 5m-режиму (env `STRATEGY`), dry-run.

---

## Task 1: Config fields

**Files:**
- Modify: `quoter/config.py` (у блоці phase-25, після `regime_min_ev`)
- Test: `tests/test_config_five.py`

- [ ] **Step 1: Write the failing test**
```python
# tests/test_config_five.py
from quoter.config import Config


def test_five_min_fields_have_defaults():
    c = Config()
    assert c.strategy == "tilt"
    assert c.lean == 3
    assert c.band_lo == 0.62
    assert c.band_hi == 0.78
```

- [ ] **Step 2: Run — expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_config_five.py -v`
Expected: FAIL (`AttributeError: ... 'strategy'`)

- [ ] **Step 3: Add fields** to `quoter/config.py`, immediately after the `regime_min_ev: float = 0.01` line:
```python

    # ── phase-26 5m early-consistent-leader strategy (separate from tilt) ──
    strategy: str = "tilt"           # "tilt" (15m momentum) | "five_min" (5m early leader)
    lean: int = 3                    # leader:laggard share ratio when accumulating
    band_lo: float = 0.62            # leader price band at minute 2 (inclusive)
    band_hi: float = 0.78
```

- [ ] **Step 4: Run — expect PASS**

Run: `.venv/bin/python -m pytest tests/test_config_five.py -v`

- [ ] **Step 5: Commit**
```bash
git add quoter/config.py tests/test_config_five.py
git commit -m "feat(config): phase-26 5m early-leader strategy fields"
```

---

## Task 2: `five_min_planner.plan_five_min` (pure)

**Files:**
- Create: `quoter/runner/five_min_planner.py`
- Test: `tests/test_five_min_planner.py`

- [ ] **Step 1: Write the failing tests**
```python
# tests/test_five_min_planner.py
from quoter.runner.five_min_planner import plan_five_min


def test_accumulate_before_min2_leans_into_leader():
    p = plan_five_min(1.0, None, None, 0.0, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.55, 0.45)
    assert p.passed is None
    assert ("YES", 0.55, 15.0) in p.orders     # leader YES: rung_size*lean = 15
    assert ("NO", 0.45, 5.0) in p.orders        # laggard: rung_size = 5


def test_min2_pass_continues():
    p = plan_five_min(2.0, "YES", "YES", 0.70, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.70, 0.30)
    assert p.passed is True
    assert ("YES", 0.70, 15.0) in p.orders


def test_min2_fail_inconsistent_leader():
    p = plan_five_min(2.0, "YES", "NO", 0.70, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.70, 0.30)
    assert p.passed is False
    assert p.orders == []


def test_min2_fail_out_of_band():
    p = plan_five_min(2.0, "YES", "YES", 0.85, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.85, 0.15)
    assert p.passed is False
    assert p.orders == []


def test_budget_exhausted_returns_empty():
    p = plan_five_min(1.0, None, None, 0.0, 15.0, 15.0, 3, 0.62, 0.78, 5, 0.55, 0.45)
    assert p.orders == []


def test_leader_is_the_higher_mid():
    p = plan_five_min(1.0, None, None, 0.0, 0.0, 15.0, 3, 0.62, 0.78, 5, 0.40, 0.60)
    assert ("NO", 0.60, 15.0) in p.orders        # NO is the leader (higher mid)
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`)

Run: `.venv/bin/python -m pytest tests/test_five_min_planner.py -v`

- [ ] **Step 3: Implement**
```python
# quoter/runner/five_min_planner.py
"""Pure planner for the 5m early-consistent-leader strategy.

Before minute 2: accumulate BOTH sides as maker bids, leaning `lean:1` into the
current leader (the side with the higher mid). At/after minute 2: apply the filter —
trade only if the leader was CONSISTENT (lead1 == lead2) AND its price is in the band.
On filter fail, post nothing (hold what was accumulated). Bounded by per_window_cap.
Pure, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FivePlan:
    orders: list           # list of (side, price, size) maker bids to post this tick
    passed: object         # bool after minute 2, else None


def plan_five_min(minute, lead1, lead2, lead_price2, spent, per_window_cap,
                  lean, band_lo, band_hi, rung_size, yes_mid, no_mid) -> FivePlan:
    passed = None
    if minute >= 2:
        passed = bool(lead1 is not None and lead1 == lead2
                      and band_lo <= lead_price2 <= band_hi)
        if not passed:
            return FivePlan([], passed)
    budget = per_window_cap - spent
    if budget <= 0 or yes_mid <= 0 or no_mid <= 0:
        return FivePlan([], passed)
    leader, lead_mid = ("YES", yes_mid) if yes_mid >= no_mid else ("NO", no_mid)
    lag, lag_mid = ("NO", no_mid) if leader == "YES" else ("YES", yes_mid)
    orders = []
    lead_size = float(rung_size * lean)
    if lead_size * lead_mid <= budget + 1e-9:
        orders.append((leader, round(lead_mid, 3), lead_size))
        budget -= lead_size * lead_mid
    lag_size = float(rung_size)
    if lag_size * lag_mid <= budget + 1e-9:
        orders.append((lag, round(lag_mid, 3), lag_size))
    return FivePlan(orders, passed)
```

- [ ] **Step 4: Run — expect PASS** (6 passed)

Run: `.venv/bin/python -m pytest tests/test_five_min_planner.py -v`

- [ ] **Step 5: Commit**
```bash
git add quoter/runner/five_min_planner.py tests/test_five_min_planner.py
git commit -m "feat(5m): pure plan_five_min — early lean + min2 consistency/band filter"
```

---

## Task 3: `paper_fill.PaperBook` (pure)

**Files:**
- Create: `quoter/runner/paper_fill.py`
- Test: `tests/test_paper_fill.py`

- [ ] **Step 1: Write the failing tests**
```python
# tests/test_paper_fill.py
from quoter.runner.paper_fill import PaperBook


def test_fills_when_price_touches_bid():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    filled = pb.on_tick("YES", 0.48)      # ask dropped to <= our 0.50 bid
    assert filled == 10.0
    assert pb.inv["YES"] == 10.0
    assert abs(pb.cost["YES"] - 5.0) < 1e-9


def test_no_fill_when_price_above_bid():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("YES", 0.55) == 0.0
    assert pb.inv["YES"] == 0.0


def test_fill_frac_haircut():
    pb = PaperBook(fill_frac=0.5)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("YES", 0.48) == 5.0


def test_wrong_side_no_fill():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("NO", 0.10) == 0.0


def test_spent_sums_cost():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10); pb.on_tick("YES", 0.40)
    pb.post("NO", 0.30, 10); pb.on_tick("NO", 0.20)
    assert abs(pb.spent() - (5.0 + 3.0)) < 1e-9


def test_price_none_no_fill():
    pb = PaperBook(fill_frac=1.0)
    pb.post("YES", 0.50, 10)
    assert pb.on_tick("YES", None) == 0.0
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError`)

Run: `.venv/bin/python -m pytest tests/test_paper_fill.py -v`

- [ ] **Step 3: Implement**
```python
# quoter/runner/paper_fill.py
"""Optimistic maker-fill estimator for dry-run paper PnL.

A resting BUY bid at price P on a side fills when that side's price touches <= P in a
later tick (a seller crosses to our bid). `fill_frac` haircuts the credited size for
queue/latency (1.0 = optimistic, fills fully on touch). Pure, no I/O. This is an
ESTIMATE — real maker fills may be worse; only a small live run settles it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Bid:
    side: str
    price: float
    remaining: float


class PaperBook:
    def __init__(self, fill_frac: float = 1.0) -> None:
        self.fill_frac = fill_frac
        self._bids: list[_Bid] = []
        self.inv = {"YES": 0.0, "NO": 0.0}
        self.cost = {"YES": 0.0, "NO": 0.0}

    def post(self, side: str, price: float, size: float) -> None:
        self._bids.append(_Bid(side, float(price), float(size)))

    def spent(self) -> float:
        return self.cost["YES"] + self.cost["NO"]

    def on_tick(self, side: str, price) -> float:
        """Fill resting bids on `side` when `price` (best offer / traded) <= bid price."""
        if price is None:
            return 0.0
        filled = 0.0
        for b in self._bids:
            if b.side == side and b.remaining > 0 and price <= b.price:
                q = float(int(b.remaining * self.fill_frac))
                if q > 0:
                    b.remaining -= q
                    self.inv[side] += q
                    self.cost[side] += q * b.price
                    filled += q
        return filled
```

- [ ] **Step 4: Run — expect PASS** (6 passed)

Run: `.venv/bin/python -m pytest tests/test_paper_fill.py -v`

- [ ] **Step 5: Commit**
```bash
git add quoter/runner/paper_fill.py tests/test_paper_fill.py
git commit -m "feat(5m): PaperBook — optimistic maker-fill estimator for dry-run PnL"
```

---

## Task 4: Wire `_five_min_window` into merge_runner

**Files:**
- Modify: `quoter/runner/merge_runner.py` (imports ~35, dispatch ~265, new method)

Live-glue; юніт-тестом не покривається (як інші `_*_window`). Перевірка: наявні тести зелені + smoke import. Пости йдуть через `_place_limit` (dry-run no-op), PnL — paper.

- [ ] **Step 1: Add imports**

Після рядка `from quoter.runner.regime_tracker import RegimeTracker` додати:
```python
from quoter.runner.five_min_planner import plan_five_min
from quoter.runner.paper_fill import PaperBook
```
І переконатись, що `import time` є на початку файлу (merge_runner має `from time import monotonic` — додати окремий рядок `import time` під ним, бо `_five_min_window` використовує `time.time()` для хвилини від `m.open_ts`).

- [ ] **Step 2: Add dispatch**

Знайти блок (рядки ~265-270):
```python
                        if self.requote and self.cfg.rungs > 1:
                            await self._ladder_window(m, mid)
                        elif self.requote:
                            await self._requote_window(m, mid)
                        else:
                            await self.trade_window(m, mid)
```
Замінити на:
```python
                        if self.cfg.strategy == "five_min":
                            await self._five_min_window(m, mid)
                        elif self.requote and self.cfg.rungs > 1:
                            await self._ladder_window(m, mid)
                        elif self.requote:
                            await self._requote_window(m, mid)
                        else:
                            await self.trade_window(m, mid)
```

- [ ] **Step 3: Add the `_five_min_window` method**

Додати новий метод у клас `MergeRunner` (одразу ПЕРЕД `def shutdown` наприкінці класу):
```python
    async def _five_min_window(self, m, mid_at_entry: float) -> None:
        """5m early-consistent-leader (dry-run + paper-fill). Accumulate a leader-leaned
        two-sided maker position from the open; at minute 2 keep only if the leader was
        consistent (min1==min2) and in-band; hold to resolution; estimate PnL via PaperBook."""
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        log.info("fivemin_enter", slug=m.slug, mid=round(mid_at_entry, 3))

        pb = PaperBook(fill_frac=max(0.1, self.cfg.paper_fill_prob_multiplier))
        lead1 = lead2 = None
        lead_price2 = 0.0
        last_yes_mid = mid_at_entry
        try:
            while m.time_remaining() > END_BUFFER_SEC:
                if self.state.force_stop_requested:
                    break
                try:
                    async with httpx.AsyncClient(timeout=6) as cl:
                        by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
                        bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
                except Exception:
                    await asyncio.sleep(REQUOTE_SEC); continue
                yb, ya = _best(by, "bids"), _best(by, "asks")
                nb, na = _best(bn, "bids"), _best(bn, "asks")
                if not (yb and ya and nb and na):
                    await asyncio.sleep(REQUOTE_SEC); continue
                yes_mid = (yb + ya) / 2
                no_mid = (nb + na) / 2
                last_yes_mid = yes_mid
                minute = (time.time() - m.open_ts) / 60.0   # minutes since window open (wall clock)

                # sample leader at ~min1 and ~min2
                cur_leader = "YES" if yes_mid >= no_mid else "NO"
                if lead1 is None and minute >= 1:
                    lead1 = cur_leader
                if lead2 is None and minute >= 2:
                    lead2 = cur_leader
                    lead_price2 = yes_mid if cur_leader == "YES" else no_mid

                plan = plan_five_min(minute, lead1, lead2, lead_price2, pb.spent(),
                                     self.cfg.per_window_cap, self.cfg.lean,
                                     self.cfg.band_lo, self.cfg.band_hi,
                                     self.cfg.rung_size, yes_mid, no_mid)
                for side, price, size in plan.orders:
                    tok = m.yes_token if side == "YES" else m.no_token
                    await self._place_limit(token_id=tok, price=price, size=size,
                                            side="BUY", post_only=True)   # dry-run: logs only
                    pb.post(side, price, size)

                # paper fills from the live book (best offer touching our bids)
                pb.on_tick("YES", ya)
                pb.on_tick("NO", na)
                self.state.pairs_caught = int(min(pb.inv["YES"], pb.inv["NO"]))
                self.state.naked_shares = int(abs(pb.inv["YES"] - pb.inv["NO"]))
                await asyncio.sleep(REQUOTE_SEC)
        finally:
            await self.cancel_all()   # dry-run no-op

        winner = "YES" if last_yes_mid >= 0.5 else "NO"
        paper_pnl = pb.inv[winner] - pb.spent()
        passed = (lead1 is not None and lead1 == lead2)
        log.info("fivemin_done", slug=m.slug, passed=passed, winner=winner,
                 paper_spent=round(pb.spent(), 2), paper_pnl=round(paper_pnl, 2),
                 inv_yes=round(pb.inv["YES"], 1), inv_no=round(pb.inv["NO"], 1))
```

**Note:** `minute` uses wall-clock `(time.time() - m.open_ts)/60`. Verify `m.open_ts` is an epoch-seconds attribute on the market object (it is used elsewhere, e.g. `self._traded_windows.add(m.open_ts)`). Only 5m windows reach here (run_control sets `timeframes=("5m",)` for this strategy).

- [ ] **Step 4: Full suite green + smoke import**

Run: `.venv/bin/python -m pytest -q`
Expected: 302 + новi (config 1 + planner 6 + paper 6 = 315) passed, no regressions.

Run: `.venv/bin/python -c "from quoter.runner.merge_runner import MergeRunner; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**
```bash
git add quoter/runner/merge_runner.py
git commit -m "feat(runner): wire _five_min_window (5m early-leader, dry-run + paper-fill)"
```

---

## Task 5: run_control 5m dry-run mode

**Files:**
- Modify: `quoter/runner/run_control.py`

- [ ] **Step 1: Add env-selected strategy**

Після рядка `from __future__ import annotations` додати `import asyncio` вже є; додати `import os` під ним. Тоді після `load_dotenv(...)` додати:
```python
STRATEGY = os.environ.get("STRATEGY", "five_min").lower()
```

- [ ] **Step 2: Build CFG by strategy**

Обгорнути наявний `CFG = Config(...)` так, щоб для `five_min` був окремий конфіг. Замінити рядок `CFG = Config(` ... до його закриваючої `)` на:
```python
if STRATEGY == "five_min":
    CFG = Config(
        strategy="five_min", assets=("BTC",), timeframes=("5m",),
        lean=3, band_lo=0.62, band_hi=0.78, rung_size=5,
        per_window_cap=15.0, per_market_cap_usd=15.0, min_time_to_expiry_sec=5.0,
        dry_run=True,                    # LIVE DISABLED
    )
else:
    CFG = Config(
        assets=("BTC",), timeframes=("15m",),
        merge_edge=0.02, flat_size=5, rung_size=5, rungs=1, rung_spacing=0.03,
        ladder_anchor="entry", max_inflight_rungs=1, naked_cap=3, min_buy_price=0.20,
        deep_ladder=False, per_window_cap=15.0, per_market_cap_usd=15.0,
        min_time_to_expiry_sec=5.0, complete_pairs=True, complete_continuous=True,
        complete_step=10, complete_gate_sec=120.0, auto_flat=False, sell_fallback=False,
        trend_enabled=True, trend_confidence=0.35, trend_gate_sec=600.0,
        tilt_enabled=True, tilt_cutoff_sec=45.0, tilt_fee=0.02, tilt_max_price=0.90,
        tilt_frac=0.65, regime_window=30, regime_min_samples=12, regime_min_ev=0.0,
        dry_run=True,                    # LIVE DISABLED
    )
assert CFG.dry_run is True, "LIVE TRADING DISABLED: CFG.dry_run must stay True"
```
(Видалити старий одиничний `CFG = Config(...)` блок і його `assert`, замінивши їх цим.)

- [ ] **Step 3: Verify both modes**

Run: `STRATEGY=five_min .venv/bin/python -c "import quoter.runner.run_control as r; print(r.CFG.strategy, r.CFG.timeframes, r.CFG.dry_run)"`
Expected: `five_min ('5m',) True`

Run: `STRATEGY=tilt .venv/bin/python -c "import quoter.runner.run_control as r; print(r.CFG.strategy, r.CFG.timeframes, r.CFG.tilt_enabled, r.CFG.dry_run)"`
Expected: `tilt ('15m',) True True`

- [ ] **Step 4: Full suite green**

Run: `.venv/bin/python -m pytest -q`
Expected: no regressions.

- [ ] **Step 5: Commit**
```bash
git add quoter/runner/run_control.py
git commit -m "feat(run_control): STRATEGY env selects five_min(5m) vs tilt(15m); both dry-run"
```

---

## Task 6: Final verification + server dry-run

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 315 passed (302 + 13), 0 failed.

- [ ] **Step 2: Safety assert (live still locked)**

Run: `STRATEGY=five_min .venv/bin/python -c "import quoter.runner.run_control as r; assert r.CFG.dry_run is True; assert r.CFG.strategy=='five_min'; print('5m dry-run locked ok')"`
Expected: `5m dry-run locked ok`

- [ ] **Step 3: Merge to master, deploy to server (dry-run)**

Deploy per `reference_aws_server_deploy`: stop service → `git archive master | ssh ... tar x` → restart with `STRATEGY=five_min` (set in the systemd unit env or a wrapper). Confirm `fivemin_enter`/`fivemin_done` logs appear and 0 real orders. **Operator-gated.** (Merging to master is a separate finishing step via superpowers:finishing-a-development-branch.)

---

## Self-Review (виконано автором)
- **Spec coverage:** §2 стратегія (Task 4 loop), §3.1 планер (Task 2), §3.2 PaperBook (Task 3), §3.3 glue (Task 4), §3.4 config+run_control (Task 1/5), §5 безпека (dry_run assert Task 5; _place_limit no-op), §6 юніт-тести (Task 2/3). Реплей-харнес (§6, опційний) — свідомо пропущено як optional/YAGNI для першого проходу; сим уже дав +2.78%.
- **Type consistency:** `plan_five_min(minute, lead1, lead2, lead_price2, spent, per_window_cap, lean, band_lo, band_hi, rung_size, yes_mid, no_mid)` однаково в Task 2 і виклику Task 4. `FivePlan.orders/.passed`, `PaperBook.post/on_tick/spent/inv/cost` однакові в Task 3 і Task 4. Сторони скрізь "YES"/"NO".
- **Placeholders:** немає; калібр `paper_fill.fill_frac` з `paper_fill_prob_multiplier` (наявне поле).
- **Ризик у Task 4:** `m.window_sec()` може не існувати → є fallback на 300s + note перевірити при реалізації.

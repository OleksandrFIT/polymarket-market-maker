# Momentum-tilt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести бота з −EV deep-ladder на трикомпонентну стратегію — база (пари <$1, maker) + directional tilt (фаворит, taker FAK) + помірна страхова нога — із circuit-breaker на paper tilt-EV.

**Architecture:** Дві нові чисті функції (`tilt_planner`, `regime_tracker`) + вбудова в наявний `_ladder_window` (той самий патерн, що блок completion). Базова сітка більше не пригнічується по тренду (лишає страхову ногу); детектор лише обирає, кого taker-докуповувати в tilt; circuit-breaker ставить tilt на паузу за рішенням paper-EV/share. Реплей-харнес калібрує параметри перед live.

**Tech Stack:** Python 3.13, pytest, наявний CLOB-клієнт (`OrderType.FAK`), Binance WS, `trend_detector`.

**Spec:** `docs/superpowers/specs/2026-06-24-momentum-tilt-design.md`

**Деталь калібрування:** `insurance_frac` (≈25%) — НЕ рантайм-поле; страховка = не-пригнічена базова лузер-нога в межах `naked_cap`. Реплей-харнес (Task 4) калібрує `naked_cap`/`min_buy_price`, щоб досягти цілі.

**Команди:** запуск тестів — `python3 -m pytest`. Працювати на гілці `feat/momentum-tilt` (вже створена).

---

## File Structure

- **Create** `quoter/runner/tilt_planner.py` — чиста функція `plan_tilt()` (скільки фаворита докупити).
- **Create** `quoter/runner/regime_tracker.py` — чистий `RegimeTracker` (circuit-breaker на paper-EV).
- **Create** `tests/test_tilt_planner.py`, `tests/test_regime_tracker.py`.
- **Create** `scripts/_replay_tilt.py` — реплей-харнес калібрування (read-only).
- **Modify** `quoter/config.py` — нові поля (секція phase-25).
- **Modify** `quoter/runner/merge_runner.py` — імпорти, `__init__`, tilt-блок у `_ladder_window`, база без trend-пригнічення, запис у tracker наприкінці вікна.
- **Modify** `quoter/runner/run_control.py` — значення CFG (Крок 1A+1B, ризик $15).

---

## Task 1: Config fields (phase-25 momentum tilt)

**Files:**
- Modify: `quoter/config.py` (вставити після рядка 85, `trend_gate_sec`)
- Test: `tests/test_config_tilt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_tilt.py
from quoter.config import Config


def test_tilt_fields_have_defaults():
    c = Config()
    assert c.tilt_enabled is False
    assert c.tilt_cutoff_sec == 45.0
    assert c.tilt_fee == 0.02
    assert c.tilt_max_price == 0.90
    assert c.regime_window == 20
    assert c.regime_min_samples == 12
    assert c.regime_min_ev == 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config_tilt.py -v`
Expected: FAIL (`AttributeError: ... 'tilt_enabled'`)

- [ ] **Step 3: Add the fields**

Вставити в `quoter/config.py` одразу після рядка `trend_gate_sec: float = 90.0` (рядок 85):

```python

    # ── phase-25 momentum tilt (directional favorite accumulation) ──
    # When trend_detector confirms a bias, TAKER-buy the favorite (side of the BTC
    # move) toward favorite_shares ≈ spent. The base ladder is NOT trend-suppressed
    # (it keeps a moderate loser leg = insurance). A circuit-breaker pauses the tilt
    # by rolling paper-EV/share (EV, not hit-rate: hit-rate is blind to entry price —
    # a favorite bought at 0.83 needs ~83% wins just to break even).
    tilt_enabled: bool = False
    tilt_cutoff_sec: float = 45.0      # no taker tilt in the last N sec of the window
    tilt_fee: float = 0.02            # taker spread estimate (CB EV + sizing margin)
    tilt_max_price: float = 0.90      # don't chase the favorite above this ask
    regime_window: int = 20           # rolling window of directional calls
    regime_min_samples: int = 12      # shadow-only warm-up until this many windows
    regime_min_ev: float = 0.01       # min paper tilt-EV/share to keep tilt enabled
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_config_tilt.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_config_tilt.py
git commit -m "feat(config): phase-25 momentum tilt + circuit-breaker fields"
```

---

## Task 2: `tilt_planner.plan_tilt()` (pure)

**Files:**
- Create: `quoter/runner/tilt_planner.py`
- Test: `tests/test_tilt_planner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tilt_planner.py
from quoter.runner.tilt_planner import plan_tilt


def test_neutral_returns_zero():
    assert plan_tilt("NEUTRAL", 0.7, 0, 5.0, 15.0, 10) == 0.0


def test_bad_ask_returns_zero():
    assert plan_tilt("UP", None, 0, 5.0, 15.0, 10) == 0.0
    assert plan_tilt("UP", 0.0, 0, 5.0, 15.0, 10) == 0.0
    assert plan_tilt("UP", 1.0, 0, 5.0, 15.0, 10) == 0.0


def test_above_max_price_returns_zero():
    assert plan_tilt("UP", 0.95, 0, 5.0, 15.0, 10, max_price=0.90) == 0.0


def test_already_enough_returns_zero():
    # inv_fav >= spent → gap <= 0
    assert plan_tilt("UP", 0.7, 10, 5.0, 15.0, 10) == 0.0


def test_sizes_toward_spent_capped_by_step():
    # gap = 8, q = 8/(1-0.6) = 20, capped by step 10
    assert plan_tilt("UP", 0.6, 0, 8.0, 100.0, 10) == 10.0


def test_budget_cap():
    # budget_left = 15 - 14 = 1 ; 1/0.5 = 2 shares (below step 10)
    assert plan_tilt("UP", 0.5, 0, 14.0, 15.0, 10) == 2.0


def test_budget_exhausted_returns_zero():
    assert plan_tilt("UP", 0.6, 0, 15.0, 15.0, 10) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tilt_planner.py -v`
Expected: FAIL (`ModuleNotFoundError: quoter.runner.tilt_planner`)

- [ ] **Step 3: Implement `plan_tilt`**

```python
# quoter/runner/tilt_planner.py
"""Pure planner: how many favorite shares to taker-buy this tick (directional tilt).

Accumulate the side BTC is trending toward. Sizing targets favorite_shares ≈ spent
(winning favorite ≈ breaks even on the matched part, profits on the edge), capped
per tick by `step` and per window by remaining budget. Pure, no I/O.
"""
from __future__ import annotations


def plan_tilt(tbias: str, fav_ask: float | None, inv_fav: float, spent: float,
              per_window_cap: float, step: int, max_price: float = 0.90) -> float:
    """Whole favorite shares to buy now; 0 when no tilt is warranted.

    tbias: "UP" | "DOWN" | "NEUTRAL". Only UP/DOWN act.
    fav_ask: favorite-side ask (taker entry). None / <=0 / >=1 → 0.
    inv_fav: favorite shares already held.
    spent: $ committed this window so far.
    per_window_cap: $ ceiling for the window.
    step: max shares to add this tick.
    max_price: don't chase the favorite above this ask.
    """
    if tbias not in ("UP", "DOWN"):
        return 0.0
    if fav_ask is None or fav_ask <= 0.0 or fav_ask >= 1.0:
        return 0.0
    if fav_ask > max_price:
        return 0.0
    gap = spent - inv_fav                 # target favorite_shares ≈ spent
    if gap <= 0:
        return 0.0
    q = gap / (1.0 - fav_ask)             # inv_fav + q == spent + q*fav_ask
    q = min(q, float(step))
    budget_left = per_window_cap - spent
    if budget_left <= 0:
        return 0.0
    q = min(q, budget_left / fav_ask)
    return float(int(q))                  # whole shares
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tilt_planner.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/tilt_planner.py tests/test_tilt_planner.py
git commit -m "feat(tilt): pure plan_tilt — favorite sizing with step/budget/price caps"
```

---

## Task 3: `RegimeTracker` circuit-breaker (pure)

**Files:**
- Create: `quoter/runner/regime_tracker.py`
- Test: `tests/test_regime_tracker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_regime_tracker.py
from quoter.runner.regime_tracker import RegimeTracker


def _fill(rt, n, fav, entry, winner):
    for _ in range(n):
        rt.record(fav, entry, winner)


def test_warmup_disables_until_min_samples():
    rt = RegimeTracker(window=20, min_samples=12, min_ev=0.01, fee=0.02)
    _fill(rt, 11, "Up", 0.80, "Up")          # 11 wins, still < 12
    assert rt.directional_enabled() is False
    rt.record("Up", 0.80, "Up")               # 12th
    assert rt.directional_enabled() is True


def test_good_regime_enabled():
    rt = RegimeTracker(window=20, min_samples=12, min_ev=0.01, fee=0.02)
    _fill(rt, 20, "Up", 0.80, "Up")           # EV = 1 - 0.82 = 0.18
    assert rt.paper_ev() > 0.01
    assert rt.directional_enabled() is True


def test_losing_regime_disables():
    rt = RegimeTracker(window=20, min_samples=12, min_ev=0.01, fee=0.02)
    for i in range(20):
        rt.record("Up", 0.83, "Up" if i % 2 == 0 else "Down")   # 50% hit
    assert rt.paper_ev() < 0
    assert rt.directional_enabled() is False


def test_thin_margin_band_disables():
    # 70% hit at entry 0.83 → EV = 0.7*(1-0.85) - 0.3*0.85 = -0.15 < 0.
    # A hit-rate CB at 0.58 would WRONGLY enable this; the EV CB correctly pauses.
    rt = RegimeTracker(window=20, min_samples=10, min_ev=0.01, fee=0.02)
    for i in range(20):
        rt.record("Up", 0.83, "Up" if i % 10 < 7 else "Down")
    assert rt.hit_rate() == 0.70
    assert rt.directional_enabled() is False


def test_window_evicts_old():
    rt = RegimeTracker(window=5, min_samples=3, min_ev=0.01, fee=0.0)
    _fill(rt, 5, "Up", 0.5, "Up")
    assert rt.hit_rate() == 1.0
    _fill(rt, 5, "Up", 0.5, "Down")           # evicts the wins
    assert rt.hit_rate() == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_regime_tracker.py -v`
Expected: FAIL (`ModuleNotFoundError: quoter.runner.regime_tracker`)

- [ ] **Step 3: Implement `RegimeTracker`**

```python
# quoter/runner/regime_tracker.py
"""Pure circuit-breaker: pause the directional tilt when its rolling paper-EV decays.

Tracks the last `window` directional calls as (predicted_fav, fav_entry_price, winner)
and computes the paper EV/share of the tilt (with taker fee). Tilt is enabled ONLY
after a shadow-only warm-up of `min_samples` windows AND while paper-EV ≥ `min_ev`.
EV (not hit-rate) is the gate: hit-rate is blind to entry price — a favorite bought
at 0.83 needs ~83% wins just to break even. Pure, no I/O.
"""
from __future__ import annotations

from collections import deque


class RegimeTracker:
    def __init__(self, window: int = 20, min_samples: int = 12,
                 min_ev: float = 0.01, fee: float = 0.02) -> None:
        self.min_samples = min_samples
        self.min_ev = min_ev
        self.fee = fee
        self._q: deque[tuple[str, float, str]] = deque(maxlen=window)

    def record(self, predicted_fav: str, entry_price: float, winner: str) -> None:
        self._q.append((predicted_fav, float(entry_price), winner))

    def _evs(self) -> list[float]:
        out = []
        for fav, entry, win in self._q:
            cost = entry + self.fee
            out.append((1.0 - cost) if fav == win else -cost)
        return out

    def paper_ev(self) -> float | None:
        evs = self._evs()
        return sum(evs) / len(evs) if evs else None

    def hit_rate(self) -> float | None:        # diagnostic only
        if not self._q:
            return None
        return sum(1 for f, _, w in self._q if f == w) / len(self._q)

    def directional_enabled(self) -> bool:
        if len(self._q) < self.min_samples:
            return False                        # shadow-only warm-up
        ev = self.paper_ev()
        return ev is not None and ev >= self.min_ev
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_regime_tracker.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/regime_tracker.py tests/test_regime_tracker.py
git commit -m "feat(regime): paper-EV circuit-breaker with shadow-only warm-up"
```

---

## Task 4: Replay calibration harness

**Files:**
- Create: `scripts/_replay_tilt.py`

Прогоняє РЕАЛЬНІ `plan_tilt` + `RegimeTracker` по пулу історичних вікон (кеш
`/tmp/poly_path15_cache` + `/tmp/poly_path15_oos`), щоб виміряти на коді: avg_fav_entry,
частоту хибних пауз CB на трендових даних, paper-EV gated-потоку. Виходи → калібрування
`regime_min_ev`, `tilt_max_price`, `min_buy_price`, `naked_cap` у Task 6.

- [ ] **Step 1: Write the harness**

```python
# scripts/_replay_tilt.py
"""Deterministic replay of the REAL tilt planner + regime tracker over cached 15m
windows. Measures avg favorite entry, CB false-pause rate on trend data, and the
gated paper-EV. Read-only; imports the live modules so we test code, not formulas."""
import sys, os, json, time, statistics, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quoter.config import Config
from quoter.runner.trend_detector import detect_bias, sigma_remaining
from quoter.runner.regime_tracker import RegimeTracker
from quoter.runner.tilt_planner import plan_tilt

UA = {"User-Agent": "Mozilla/5.0"}
DIRS = ["/tmp/poly_path15_cache", "/tmp/poly_path15_oos"]
STEP = 900
DECISION_MIN = 10


def get(u, tries=3):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.5)


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


cfg = Config(trend_confidence=0.35, trend_gate_sec=600.0)
rt = RegimeTracker(cfg.regime_window, cfg.regime_min_samples, cfg.regime_min_ev, cfg.tilt_fee)
entries, gated_ev, paused, fired, false_pause = [], [], 0, 0, 0
for w in wins:
    strike = btc(w["ts"]); now = btc(w["ts"] + DECISION_MIN * 60)
    if strike is None or now is None:
        continue
    buf = [(btc(w["ts"] + k * 60), float(w["ts"] + k * 60))
           for k in range(max(0, DECISION_MIN - 8), DECISION_MIN + 1) if btc(w["ts"] + k * 60) is not None]
    sig = sigma_remaining(buf, 900 - DECISION_MIN * 60, cfg)
    bias = detect_bias(now, strike, sig, 900 - DECISION_MIN * 60, cfg)
    if bias == "NEUTRAL":
        continue
    fav = "Up" if bias == "UP" else "Down"
    entry = w["up"][DECISION_MIN] if fav == "Up" else 1 - w["up"][DECISION_MIN]
    entries.append(entry)
    enabled = rt.directional_enabled()
    win = w["winner"]
    realized = (1 - (entry + cfg.tilt_fee)) if fav == win else -(entry + cfg.tilt_fee)
    if enabled:
        fired += 1; gated_ev.append(realized)
    else:
        paused += 1
        if realized > 0:
            false_pause += 1            # paused a window that would have been +EV
    rt.record(fav, entry, win)

print("\nsignals: %d  | tilt FIRED: %d  | tilt PAUSED: %d" % (len(entries), fired, paused))
print("avg_fav_entry: %.3f (median %.3f)" % (statistics.mean(entries), statistics.median(entries)))
print("breakeven hit (entry+fee): %.3f" % (statistics.mean(entries) + cfg.tilt_fee))
if gated_ev:
    print("gated paper-EV/share: %+.4f  (n=%d)" % (statistics.mean(gated_ev), len(gated_ev)))
print("false-pause rate (paused but +EV): %.0f%% of paused" % (100 * false_pause / paused if paused else 0))
print("\nCALIBRATE: set regime_min_ev so gated-EV stays >0; tilt_max_price near avg_fav_entry+margin.")
```

- [ ] **Step 2: Run it and record numbers**

Run: `python3 scripts/_replay_tilt.py 2>&1 | grep -vE "Deprecation"`
Expected: prints avg_fav_entry (~0.81), gated paper-EV/share (>0), false-pause rate.
**Дія:** записати avg_fav_entry і gated-EV у коментар Task 6; якщо gated-EV ≤ 0 — підняти `regime_min_ev`; якщо false-pause надто високий (>30%) — знизити `regime_min_ev` чи `regime_min_samples`.

- [ ] **Step 3: Commit**

```bash
git add scripts/_replay_tilt.py
git commit -m "test(replay): tilt+regime calibration harness over cached windows"
```

---

## Task 5: Wire tilt into `merge_runner._ladder_window`

**Files:**
- Modify: `quoter/runner/merge_runner.py` (imports ~35, `__init__` ~78, loop ~561, done ~603)

Цей шар — live-glue; юніт-тестом не покривається (як решта `_ladder_window`). Перевірка:
наявні тести лишаються зеленими + ручний live ($15). Чисті планери вже протестовані (Task 2-3).

- [ ] **Step 1: Add imports**

У `quoter/runner/merge_runner.py` після рядка 35 (`from quoter.runner.trend_detector import detect_bias, sigma_remaining`) додати:

```python
from quoter.runner.tilt_planner import plan_tilt
from quoter.runner.regime_tracker import RegimeTracker
```

- [ ] **Step 2: Init the tracker**

У `MergeRunner.__init__`, після рядка 78 (`self._binance = ...`) додати:

```python
        self._regime = RegimeTracker(cfg.regime_window, cfg.regime_min_samples,
                                     cfg.regime_min_ev, cfg.tilt_fee)
```

- [ ] **Step 3: Per-window shadow state**

У `_ladder_window`, одразу після рядка 402 (`strike = self._btc_buf[-1][0] if self._btc_buf else None`) додати:

```python
        last_bias = "NEUTRAL"          # last non-NEUTRAL detector call this window
        last_fav_entry = 0.0           # favorite ask at that call (shadow/real entry)
        last_mid = mid_at_entry        # last seen YES mid (for end-of-window winner)
```

- [ ] **Step 4: Base ladder must NOT be trend-suppressed; add the tilt block**

Замінити блок рядків 561-568 (обчислення `tbias`, `last_event`, виклик `plan_ladder`) на:

```python
                tbias = self._trend_bias(strike, m.time_remaining())
                # track shadow entry for the circuit-breaker (even when tilt is paused)
                if tbias != "NEUTRAL":
                    last_bias = tbias
                    fav_ask_now = yes_ask if tbias == "UP" else no_ask
                    if fav_ask_now:
                        last_fav_entry = fav_ask_now
                if yes_bid and yes_ask:
                    last_mid = (yes_bid + yes_ask) / 2

                # DIRECTIONAL TILT: taker-buy the favorite when the trend is confirmed
                # AND the circuit-breaker says the edge is alive. Base stays two-sided
                # (insurance leg) — tilt is the only thing that uses the bias.
                if (self.cfg.tilt_enabled and tbias != "NEUTRAL"
                        and self._regime.directional_enabled()
                        and m.time_remaining() > self.cfg.tilt_cutoff_sec):
                    fav_side = "YES" if tbias == "UP" else "NO"
                    fav_ask = yes_ask if fav_side == "YES" else no_ask
                    inv_fav = inv_yes if fav_side == "YES" else inv_no
                    cq = plan_tilt(tbias, fav_ask, inv_fav, realized,
                                   self.cfg.per_window_cap, self.cfg.complete_step,
                                   self.cfg.tilt_max_price)
                    if cq > 0 and fav_ask:
                        tok = m.yes_token if fav_side == "YES" else m.no_token
                        r = await self.clob.place_limit(
                            token_id=tok, price=fav_ask, size=cq,
                            side="BUY", post_only=False, order_type="FAK")
                        if r and r.get("order_id"):
                            posted[fav_side] += cq
                            log.info("runner_tilt", slug=m.slug, side=fav_side,
                                     qty=cq, price=round(fav_ask, 3))

                self.state.last_event = f"laddering {m.slug} (bias {tbias})"
                # base ladder: NEUTRAL → never trend-suppress the loser (= insurance leg)
                plan = plan_ladder(
                    yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask,
                    entry_mid=entry_mid, inv_yes=inv_yes, inv_no=inv_no,
                    yes_cost=inv.cost["YES"], no_cost=inv.cost["NO"],
                    committed=committed, resting=resting, cfg=self.cfg,
                    trend_bias="NEUTRAL", suppressed=frozenset(flattened))
```

- [ ] **Step 5: Record the window outcome into the tracker**

У `_ladder_window`, у блоці завершення (після рядка 606, `log.info("runner_ladder_done", ...)`) додати:

```python
        if last_bias != "NEUTRAL":
            winner = "Up" if last_mid >= 0.5 else "Down"
            pred = "Up" if last_bias == "UP" else "Down"
            self._regime.record(pred, last_fav_entry, winner)
            log.info("runner_regime_record", slug=m.slug, pred=pred,
                     entry=round(last_fav_entry, 3), winner=winner,
                     paper_ev=round(self._regime.paper_ev() or 0.0, 4),
                     enabled=self._regime.directional_enabled())
```

- [ ] **Step 6: Run the full suite — no regressions**

Run: `python3 -m pytest -q`
Expected: усі наявні тести + нові зелені.

- [ ] **Step 7: Smoke-import the runner**

Run: `python3 -c "from quoter.runner.merge_runner import MergeRunner; print('ok')"`
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add quoter/runner/merge_runner.py
git commit -m "feat(runner): wire directional tilt + regime CB into _ladder_window; base no longer trend-suppressed"
```

---

## Task 6: run_control.py live config (Крок 1A + 1B + ризик $15)

**Files:**
- Modify: `quoter/runner/run_control.py` (об'єкт `CFG`, рядки 30-73)

- [ ] **Step 1: Replace the CFG block**

Замінити поля в `CFG = Config(...)` на (значення `min_buy_price`/`naked_cap` — з калібрування Task 4; нижче стартові гіпотези):

```python
CFG = Config(
    assets=("BTC",), timeframes=("15m",),
    # base maker ladder (near-mid, two-sided) — pairs <$1 + insurance leg
    merge_edge=0.02, flat_size=5, rung_size=5, rungs=2, rung_spacing=0.03,
    ladder_anchor="entry", max_inflight_rungs=1,
    naked_cap=5,                    # base imbalance bound (calibrate via _replay_tilt)
    min_buy_price=0.20,             # insurance allowed cheaper than 0.42 (calibrate)
    deep_ladder=False,              # OFF: the −EV deep-catch is gone
    # risk: ~$15/window
    per_window_cap=15.0, per_market_cap_usd=15.0, min_time_to_expiry_sec=5.0,
    # pair completion (continuous, never sell — guru-style)
    complete_pairs=True, complete_continuous=True, complete_step=10,
    complete_gate_sec=120.0, auto_flat=False, sell_fallback=False,
    # trend detector ON (drives the tilt only; base is NEUTRAL)
    trend_enabled=True, trend_confidence=0.35, trend_gate_sec=600.0,
    # directional tilt + circuit-breaker
    tilt_enabled=True, tilt_cutoff_sec=45.0, tilt_fee=0.02, tilt_max_price=0.90,
    regime_window=20, regime_min_samples=12, regime_min_ev=0.01,
)
```

- [ ] **Step 2: Smoke-run import (no live start)**

Run: `python3 -c "import quoter.runner.run_control as r; print(r.CFG.tilt_enabled, r.CFG.per_window_cap)"`
Expected: `True 15.0`

- [ ] **Step 3: Full suite green**

Run: `python3 -m pytest -q`
Expected: усі зелені.

- [ ] **Step 4: Commit**

```bash
git add quoter/runner/run_control.py
git commit -m "feat(run_control): Крок-1 live config — base+tilt+CB, BTC 15m, $15/window"
```

---

## Task 7: Final verification + live checklist

- [ ] **Step 1: Full suite**

Run: `python3 -m pytest -q`
Expected: 0 failed.

- [ ] **Step 2: Confirm no −EV deep-ladder remnants in live config**

Run: `python3 -c "import quoter.runner.run_control as r; assert r.CFG.deep_ladder is False; assert r.CFG.trend_enabled and r.CFG.tilt_enabled; print('config ok')"`
Expected: `config ok`

- [ ] **Step 3: Manual live validation (operator-gated, not automated)**

Запустити `.venv/bin/python -m quoter.runner.run_control`, відкрити дашборд, натиснути START на малому розмірі. Спостерігати логи: `runner_tilt` (докупівля фаворита), `runner_regime_record` (paper_ev/enabled), `runner_ladder_fillquality`. Зібрати ≥150 вікон, звірити з §8 spec (realized fill фаворита vs 0.815, внесок tilt/база/страховка, спрацювання CB).

- [ ] **Step 4: Final commit (if any tweaks)**

```bash
git add -A && git commit -m "chore(momentum-tilt): Крок-1 ready for live validation"
```

---

## Self-Review (виконано автором плану)

- **Spec coverage:** база-maker (Task 5 NEUTRAL plan_ladder), tilt-taker-FAK (Task 5), CB на paper-EV (Task 3), shadow warm-up (Task 3), no-tilt cutoff + max_price (Task 1/5), реплей-харнес + no-peeking (Task 4; no-peeking забезпечено тим, що `plan_tilt` не приймає майбутніх даних), каппи/безпека (Task 5/6), значення 1A+ризик (Task 6). Покрито.
- **Insurance:** реалізовано через не-пригнічену базу (NEUTRAL) у межах `naked_cap`; `insurance_frac` — ціль калібрування (Task 4), не рантайм-поле (свідома мінімізація проти спеки).
- **Type consistency:** `plan_tilt(tbias, fav_ask, inv_fav, spent, per_window_cap, step, max_price)` однаково у Task 2 і виклику Task 5. `RegimeTracker(window, min_samples, min_ev, fee)` і `.record(pred, entry, winner)`/`.directional_enabled()`/`.paper_ev()` однакові у Task 3 і Task 5. Vocab переможця/прогнозу — "Up"/"Down" усюди.
- **Placeholders:** немає; калібровані числа позначені як такі з робочими стартовими значеннями.

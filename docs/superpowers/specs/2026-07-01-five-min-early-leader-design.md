# Spec: 5m early-consistent-leader maker-lean strategy (dry-run + paper-fill)

**Дата:** 2026-07-01
**Статус:** затверджено до реалізації
**Пам'ять:** [[project-momentum-edge]], [[reference_polymarket_pnl_data]]

## 1. Мета й доказова база

Додати НОВУ, окрему стратегію для **5-хвилинних** BTC up/down вікон, реверс-інженеровану
з прибуткового конкурента (`0xb27b`, +$759k all-time, $2.34М обороту/добу, едж ~0.4%).
Наш поточний 15m momentum-tilt **затух** (пізній taker-вхід @0.83) і лишається недоторканим.

**Доказ (сим на 4500 5m-вікон, 15.7 дня, `_early_lean_5m.py`/`_oos_validate.py`):**
- Наївний «lean у мувера в усіх вікнах» → +0.05% (нікчемно, вмирає від тертя).
- **СЕЛЕКЦІЯ — пропущений едж:** торгувати ЛИШЕ коли ранній лідер ПОСЛІДОВНИЙ
  (та сама сторона попереду на хв1 І хв2) → **+2.59% обороту**; + смуга входу 0.62–0.78 → **+2.78%**.
- **Стабільно, не оверфіт:** потижнево +1.89%/+3.42%/+3.68% (зростає, не затухає).
- **Виживає тертя:** 0.5¢ → +1.88%, 1.0¢ → +0.99%, лише 2.0¢ (повний taker) → −0.74%.
  Тобто **як MAKER (0.5–1¢) впевнено +1–1.9%.**
- Механізм відтворює конкурента: вхід переможця ~0.61, лузера ~0.44, heavy=winner 73%.

**Застереження:** сим філить по mid (ідеальний maker); реальні лімітні філли не гарантовані.
Тому валідація — **dry-run + paper-fill модель** (оцінка PnL, 0 реальних ордерів). Live лишається
замкнено (`dry_run=True` assert у run_control).

## 2. Стратегія (один 5m-цикл)

1. Дискавер 5m BTC-вікна на відкритті (mid у балансі, свіже, ≥ мін. час).
2. **Накопичення з хв0:** щотік постити **post-only maker-біди на ОБИДВІ сторони, з перекосом
   у поточного лідера** (сторона з mid>0.5), співвідношення `lean` (≈3:1 лідер:лаггард).
3. **Фільтр на хв2:** лідер(хв1) == лідер(хв2) **І** ціна лідера ∈ [`band_lo`, `band_hi`] (≈0.62–0.78)?
   - **PASS** → продовжувати тримати/добирати перекіс до `per_window_cap`.
   - **FAIL** → стоп добору (більше не постити), тримати те мале, що назбиралось.
4. **Тримати до resolution** (5 хв), НЕ продавати. Переможець → paper-payout.

Причинність: рішення на хв K використовує лише ціни до хв K; переможець — фінальний mid. Без look-ahead.

## 3. Компоненти (ізольовані, тестовані)

### 3.1 `quoter/runner/five_min_planner.py` (новий, чистий)
```
plan_five_min(minute, lead1, lead2, lead_price2, inv_lead, inv_lag, spent,
              per_window_cap, lean, band_lo, band_hi, rung_size, mid_lead, mid_lag) -> FivePlan
```
- До хв2 (accumulate phase): повертає біди на ОБИДВІ сторони, лідер×`lean`, лаггард×1 (post-only).
- На/після хв2: обчислює `passed = (lead1 == lead2) and band_lo <= lead_price2 <= band_hi`.
  - `passed` → продовжувати добір перекосу до бюджету.
  - not `passed` → порожній план (стоп; тримати наявне).
- Усе обмежено `per_window_cap` (spent + intended ≤ cap).
- `FivePlan`: список `(side, price, size)` + `passed: bool|None` (None до хв2).
- **Чистий, без I/O. Юніт-тести.**

### 3.2 `quoter/runner/paper_fill.py` (новий, чистий)
```
class PaperBook:  # tracks posted paper limits, credits fills from the live price stream
    def post(self, side, price, size, ts)
    def on_tick(self, side, best_price, ts) -> filled_qty   # fills when price touches <= our bid
```
- Maker-fill модель: наш бід @P на side філиться, коли `best_price[side] <= P` у наступному тіку
  (taker продав у нас). Haircut на чергу/латентність через наявні `cfg.paper_queue_position`/
  `paper_latency_ms` (проста ймовірнісна знижка). **Оптимістична — позначено.**
- Веде paper-інвентар (`inv[side]`, `cost[side]`). **Чистий, без I/O. Юніт-тести.**

### 3.3 `quoter/runner/merge_runner._five_min_window` (glue, новий метод)
- Викликається з `run_forever`, коли `cfg.strategy == "five_min"` і вікно 5m.
- Семплити mid на ~хв1 (≥60с) і ~хв2 (≥120с) від `m.open_ts`.
- Щотік (кожні `REQUOTE_SEC`): read book → `plan_five_min(...)` → для кожного intended:
  `_place_limit(post_only=True)` (dry-run логує `fivemin_place`, 0 реальних) → `PaperBook.post(...)`.
- Щотік годувати `PaperBook.on_tick(best_bid/ask)` → paper-філли → paper inv/cost.
- Кінець вікна: `winner` з фінального mid; **paper PnL = paper_inv[winner] − paper_spent**;
  лог `fivemin_done` (passed, paper_pnl, paper_spent, winner, fills).
- Cleanup `cancel_all` (no-op у dry-run).

### 3.4 `quoter/config.py` + `run_control.py`
Нові поля `Config`:
- `strategy: str = "tilt"` — `"tilt"` (наявний 15m) | `"five_min"` (нова).
- `lean: int = 3` — перекіс лідер:лаггард.
- `band_lo: float = 0.62`, `band_hi: float = 0.78` — смуга ціни лідера на хв2.
- (перевикористати `rung_size`, `per_window_cap`, `paper_*`).

`run_control.py` (окремий 5m-режим; live лишається замкнено `dry_run=True`):
- `strategy="five_min"`, `assets=("BTC",)`, `timeframes=("5m",)`, `lean=3`,
  `band_lo=0.62`, `band_hi=0.78`, `rung_size=5`, `per_window_cap=15.0`, `dry_run=True`.

## 4. Потік даних
```
discover 5m → sample mid@min1, mid@min2 →
  each tick: read book →
    plan_five_min(minute, lead1, lead2, lead_price2, inv, spent, cap, lean, band) -> FivePlan(orders, passed)
      -> _place_limit(post_only) [dry-run: log "fivemin_place", 0 real]
      -> PaperBook.post(order)
    PaperBook.on_tick(best_prices) -> paper fills -> paper inv/cost
  window end: winner=final mid>=0.5 ? Up:Down
    paper_pnl = paper_inv[winner] - paper_spent  -> log "fivemin_done"
```

## 5. Обробка помилок / безпека
- **Live замкнено** (`dry_run=True` + assert у run_control) — 0 реальних ордерів (доведено рев'ю).
- Немає книги/семплів mid → пропустити вікно (не торгувати наосліп).
- Усе під `per_window_cap`; `_place_limit`/`_cancel_orders`/`cancel_all` — dry-run no-op.
- Paper-PnL — ОЦІНКА (оптимістичний maker-fill); у логах позначено як paper, не realized.
- Старт STOPPED; оператор тисне START (dry-run).

## 6. Тестування
- **Юніт `five_min_planner`:** accumulate-фаза постить обидві сторони з lean; на хв2 PASS
  (lead1==lead2 & у смузі) продовжує, FAIL (стрибок лідера АБО поза смугою) → порожній план;
  межа `per_window_cap`; лаггард×1.
- **Юніт `paper_fill`:** філ при `best_price <= bid`; без-філ коли ціна не торкається; черга-haircut;
  інвентар/кост коректні.
- **Реплей-харнес** (`scripts/_five_replay.py`, опційно): прогнати `plan_five_min` + `PaperBook`
  по кешу 5m-шляхів → звірити paper-EV з +2.78% сим (тими самими правилами).
- **Інтеграція `_five_min_window`:** оператор-gated dry-run на сервері (paper-PnL збирається, 0 ордерів).
- Уся наявна тестова база (302) лишається зеленою; 15m-tilt-шлях не змінюється.

## 7. Відкриті ризики
- **Paper-fill оптимістичний** (філ при торканні ціни, без повної черги) → paper-PnL — стеля.
  Реальні maker-філли можуть бути гірші; остаточно підтвердить лише малий live (окреме рішення).
- **Смуга 0.62–0.78 частково in-sample** (з тих самих 15 днів) — моніторити, чи тримається на нових днях.
- **Селекція до ~29% вікон** → менше угод; на dry-run перевірити реальну частоту входів.
- 5m — швидкий цикл; семплінг mid на хв1/хв2 має бути надійним попри лаг книги.

## 8. Поза обсягом (YAGNI)
- Реальне live-виконання (замкнено; окреме свідоме рішення).
- Заміна/зміна 15m-tilt (лишається як є).
- HFT-масштаб/латентність-оптимізація (наш обсяг ≪ конкурента; спершу довести сигнал у paper).
- On-chain merge/redeem автоматизація.

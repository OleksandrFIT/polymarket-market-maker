# Top-of-Book Live MM Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the live-capable top-of-book MM mode (quoter + auto-merge + auto-redeem) behind the existing dry-run lock, replicating the validated +1.1%-edge strategy (`scripts/_top_book.py`, spec `docs/superpowers/specs/2026-07-02-top-book-live-mm-design.md`).

**Architecture:** Pure planner (`plan_top_book`/`diff_quotes`/`plan_merge`) + a `strategy=="top_book"` branch in `merge_runner` reusing the proven dry-run-locked order wrappers and safety rails; on-chain `positions_ops` (merge/redeem via proxy — relayer spike with web3 fallback); an independent redeem sweeper; new dashboard metrics.

**Tech Stack:** Python 3.13, httpx, pytest, existing `py-clob-client-v2`; `web3` only if the spike falls back to direct on-chain. `.venv/bin/python`, branch `master`.

**HARD RULES for every task:** `dry_run=True` stays asserted; never place a real order; the spike's one real $1 merge runs ONLY after the operator's explicit ok; never sell.

**Conventions:** sides `"Up"`/`"Down"`; book level lists come from CLOB `/book` as `{"bids":[{"price","size"},...],"asks":[...]}` with bids ASCENDING and asks DESCENDING (top of book = max bid / min ask); tick = 0.001.

---

### Task 1: Config (phase-27)

**Files:**
- Modify: `quoter/config.py` (append fields next to the phase-26 block)
- Test: `tests/test_config_top_book.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_top_book.py`:

```python
from quoter.config import Config


def test_top_book_defaults():
    c = Config()
    assert c.tb_size == 5.0
    assert c.tb_naked_cap == 10.0
    assert c.tb_tick == 0.001
    assert c.tb_merge_min == 5.0


def test_top_book_strategy_selectable():
    c = Config(strategy="top_book")
    assert c.strategy == "top_book"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_top_book.py -q`
Expected: FAIL (AttributeError: tb_size).

- [ ] **Step 3: Add fields to `quoter/config.py`** (after the phase-26 five_min block; keep dataclass style of the file):

```python
    # ── phase-27 top-of-book MM strategy ──
    tb_size: float = 5.0          # shares per quote per side
    tb_naked_cap: float = 10.0    # stop quoting a side when inv[side]-inv[other] >= cap
    tb_tick: float = 0.001        # price-improvement tick over best bid
    tb_merge_min: float = 5.0     # merge matched pairs once min(inv) >= this
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_top_book.py -q` → PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/config.py tests/test_config_top_book.py
git commit -m "feat(topbook): phase-27 config fields (tb_size/naked_cap/tick/merge_min)"
```

---

### Task 2: Pure planner — plan_top_book + diff_quotes + plan_merge

**Files:**
- Create: `quoter/runner/top_book_planner.py`
- Test: `tests/test_top_book_planner.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_top_book_planner.py`:

```python
from quoter.runner.top_book_planner import plan_top_book, diff_quotes, plan_merge, TBQuote


def B(levels):  # CLOB-style book side
    return [{"price": str(p), "size": str(s)} for p, s in levels]


YES = {"bids": B([(0.01, 100), (0.48, 50)]), "asks": B([(0.99, 100), (0.52, 40)])}
NO = {"bids": B([(0.01, 100), (0.46, 30)]), "asks": B([(0.99, 100), (0.54, 20)])}


def test_quotes_best_plus_tick_both_sides():
    qs = plan_top_book(YES, NO, inv_up=0, inv_dn=0, naked_cap=10, size=5, tick=0.001)
    d = {q.side: q for q in qs}
    assert d["Up"].price == 0.481          # max bid 0.48 + tick
    assert d["Down"].price == 0.461
    assert d["Up"].size == 5 and d["Down"].size == 5


def test_never_cross_the_spread():
    tight = {"bids": B([(0.52, 10)]), "asks": B([(0.521, 10)])}   # bid+tick == ask
    qs = plan_top_book(tight, NO, 0, 0, 10, 5, 0.001)
    assert all(q.side != "Up" for q in qs)


def test_gate_099():
    hi = {"bids": B([(0.99, 10)]), "asks": B([])}
    qs = plan_top_book(hi, NO, 0, 0, 10, 5, 0.001)
    assert all(q.side != "Up" for q in qs)


def test_skew_cap_stops_heavy_side():
    qs = plan_top_book(YES, NO, inv_up=15, inv_dn=0, naked_cap=10, size=5, tick=0.001)
    assert all(q.side != "Up" for q in qs)
    assert any(q.side == "Down" for q in qs)


def test_empty_bids_skips_side():
    qs = plan_top_book({"bids": [], "asks": B([(0.6, 5)])}, NO, 0, 0, 10, 5, 0.001)
    assert all(q.side != "Up" for q in qs)


def test_diff_quotes_keep_cancel_post():
    cur = {"Up": (0.481, 5.0), "Down": (0.40, 5.0)}      # Down price now stale
    tgt = [TBQuote("Up", 0.481, 5.0), TBQuote("Down", 0.461, 5.0)]
    cancel, post = diff_quotes(cur, tgt)
    assert cancel == ["Down"]
    assert [(q.side, q.price) for q in post] == [("Down", 0.461)]


def test_diff_quotes_cancels_side_missing_from_target():
    cur = {"Up": (0.481, 5.0)}
    cancel, post = diff_quotes(cur, [])                   # Up gated out now
    assert cancel == ["Up"] and post == []


def test_plan_merge():
    assert plan_merge(12.0, 7.0, merge_min=5.0) == 7.0
    assert plan_merge(3.0, 7.0, merge_min=5.0) == 0.0     # min(3,7)=3 < 5
    assert plan_merge(0.0, 7.0, merge_min=5.0) == 0.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_top_book_planner.py -q`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Write the implementation**

`quoter/runner/top_book_planner.py`:

```python
"""Pure top-of-book MM planner (phase-27). Bid best+tick on BOTH sides (price improvement
-> alone at our level, queue ahead = 0), skew-cap naked inventory, never cross, never sell.
Validated offline: +1.1% edge / 68% win / 97% matched on 197 real windows."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TBQuote:
    side: str      # "Up" | "Down"
    price: float
    size: float


def _best_bid(book) -> float | None:
    bids = (book or {}).get("bids", [])
    return max((float(l["price"]) for l in bids), default=None)


def _best_ask(book) -> float | None:
    asks = (book or {}).get("asks", [])
    return min((float(l["price"]) for l in asks), default=None)


def plan_top_book(yes_book, no_book, inv_up: float, inv_dn: float,
                  naked_cap: float, size: float, tick: float = 0.001) -> list[TBQuote]:
    out: list[TBQuote] = []
    inv = {"Up": inv_up, "Down": inv_dn}
    for side, book in (("Up", yes_book), ("Down", no_book)):
        other = "Down" if side == "Up" else "Up"
        if inv[side] - inv[other] >= naked_cap:
            continue
        bb = _best_bid(book)
        if bb is None:
            continue
        our = round(bb + tick, 3)
        ba = _best_ask(book)
        if ba is not None and our >= ba:
            continue
        if our >= 0.99:
            continue
        out.append(TBQuote(side, our, float(size)))
    return out


def diff_quotes(current: dict, target: list[TBQuote]):
    """current: {side: (price, size)} of what we have resting.
    Returns (sides_to_cancel, quotes_to_post). Unchanged quotes are kept (queue priority)."""
    tgt = {q.side: q for q in target}
    cancel = [s for s, (p, sz) in current.items()
              if s not in tgt or (tgt[s].price, tgt[s].size) != (p, sz)]
    post = [q for q in target
            if q.side not in current or (current[q.side][0], current[q.side][1]) != (q.price, q.size)]
    return cancel, post


def plan_merge(inv_up: float, inv_dn: float, merge_min: float) -> float:
    m = min(inv_up, inv_dn)
    return m if m >= merge_min else 0.0
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_top_book_planner.py -q` → PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/top_book_planner.py tests/test_top_book_planner.py
git commit -m "feat(topbook): pure planner (plan_top_book + diff_quotes + plan_merge)"
```

---

### Task 3: Runner glue — `_top_book_window`

**Files:**
- Modify: `quoter/runner/merge_runner.py` (imports, dispatch, new method)

READ the file first; adapt to its exact helper signatures — the patterns below match `_five_min_window` (built earlier the same way). No new unit tests (glue is dry-run-verified in Task 7); the pure logic it calls is already tested.

- [ ] **Step 1: Add import + dispatch**

Imports: `from quoter.runner.top_book_planner import plan_top_book, diff_quotes, plan_merge`.
In `run_forever`'s enter branch, BEFORE the five_min check:

```python
                    if enter:
                        if self.cfg.strategy == "top_book":
                            await self._top_book_window(m, mid)
                        elif self.cfg.strategy == "five_min":
```

- [ ] **Step 2: Add the method** (same structure as `_five_min_window`: mark window traded, loop until expiry, use `self._place_limit`/`self._cancel_orders` which are ALREADY dry-run no-ops):

```python
    async def _top_book_window(self, m, mid_at_entry: float) -> None:
        """Top-of-book MM (phase-27): keep bid best+tick on BOTH sides, skew-cap naked,
        merge matched pairs (via positions_ops when live; logged intent in dry-run),
        never sell. Spend bounded by per_window_cap even if merge fails."""
        self._traded_windows.add(m.open_ts)
        self.state.windows_traded += 1
        self.state.last_window = m.slug
        self.state.last_event = f"topbook {m.slug}"
        log.info("topbook_enter", slug=m.slug, mid=round(mid_at_entry, 3))
        inv = {"Up": 0.0, "Down": 0.0}     # filled (LocalInventory-style credit below)
        cost = {"Up": 0.0, "Down": 0.0}
        posted_usd = 0.0                    # lag-proof spend backstop (counts what we SEND)
        resting: dict = {}                  # side -> (price, size); oids: side -> list[str]
        oids: dict = {"Up": [], "Down": []}
        merged = 0.0
        tok = {"Up": m.yes_token, "Down": m.no_token}
        async with httpx.AsyncClient(timeout=8) as cl:
            while m.time_remaining() > self.cfg.min_time_to_expiry_sec and not self._shutdown:
                if self.state.drain_force_stop():
                    await self.cancel_all()
                    return
                by = (await cl.get(f"{CLOB}/book", params={"token_id": tok['Up']})).json()
                bn = (await cl.get(f"{CLOB}/book", params={"token_id": tok['Down']})).json()
                target = plan_top_book(by, bn, inv["Up"], inv["Down"],
                                       self.cfg.tb_naked_cap, self.cfg.tb_size, self.cfg.tb_tick)
                # spend cap: drop new posts once posted notional would exceed the cap
                target = [q for q in target
                          if posted_usd + q.price * q.size <= self.cfg.per_window_cap + 1e-9
                          or q.side in resting]
                cancel, post = diff_quotes(resting, target)
                for side in cancel:
                    await self._cancel_orders(oids.get(side, []))
                    oids[side] = []
                    resting.pop(side, None)
                for q in post:
                    r = await self._place_limit(token_id=tok[q.side], price=q.price,
                                                size=q.size, side="BUY", post_only=True)
                    if r:
                        oids[q.side] = [r]
                    resting[q.side] = (q.price, q.size)
                    posted_usd += q.price * q.size
                # fills: credit-on-vanish (LIVE) — in dry_run open-orders are empty and
                # resting was never real, so nothing credits (mechanics-only dry run).
                if not self.cfg.dry_run:
                    open_ids = await self._open_order_ids()
                    for side in ("Up", "Down"):
                        gone = [o for o in oids[side] if o not in open_ids]
                        if gone and side in resting:
                            p, sz = resting.pop(side)
                            oids[side] = []
                            inv[side] += sz
                            cost[side] += sz * p
                            log.info("topbook_fill", side=side, price=p, size=sz)
                # merge matched pairs
                mq = plan_merge(inv["Up"], inv["Down"], self.cfg.tb_merge_min)
                if mq > 0:
                    ok = await self._merge_pairs(m, mq)   # dry_run -> logs intent, True
                    if ok:
                        inv["Up"] -= mq
                        inv["Down"] -= mq
                        merged += mq
                        self.state.merged_today += mq
                await asyncio.sleep(LOOP_SEC)
        await self.cancel_all()
        log.info("topbook_done", slug=m.slug, merged=merged,
                 inv_up=inv["Up"], inv_dn=inv["Down"],
                 spent=round(cost["Up"] + cost["Down"], 2), posted=round(posted_usd, 2))
```

Add the two small helpers next to it (adapt to existing client wrapper):

```python
    async def _open_order_ids(self) -> set:
        try:
            return {o.get("id") for o in (self.clob.get_open_orders() or [])}
        except Exception:
            return set()

    async def _merge_pairs(self, m, qty: float) -> bool:
        if self.cfg.dry_run:
            log.info("dryrun_merge", slug=m.slug, qty=qty)
            return True
        from quoter.chain.positions_ops import merge_pairs
        try:
            return merge_pairs(m.market_id, qty)
        except Exception as e:
            log.warning("merge_failed", error=str(e))
            return False
```

(`CLOB = "https://clob.polymarket.com"` — reuse the module's existing constant if present; `self.clob` = the existing client wrapper used by `_requote_window`; adapt the open-orders call to its actual method name.)

- [ ] **Step 3: Sanity — suite still green**

Run: `.venv/bin/python -m pytest -q` → all pass (glue adds no test yet; imports must not break).

- [ ] **Step 4: Commit**

```bash
git add quoter/runner/merge_runner.py
git commit -m "feat(topbook): runner glue (_top_book_window: diff-quoting, spend cap, merge hook)"
```

---

### Task 4: State metrics + run_control

**Files:**
- Modify: `quoter/runner/trading_state.py` (add `merged_today`, `redeemed_today` fields, include in `snapshot()`)
- Modify: `quoter/runner/run_control.py` (STRATEGY branch `top_book`)
- Test: `tests/test_run_control_cfg.py` (add case)

- [ ] **Step 1: Write the failing test** (append to `tests/test_run_control_cfg.py`, reusing its `_load_rc` helper):

```python
def test_top_book_cfg_is_dry_run():
    rc = _load_rc("top_book")
    c = rc.CFG
    assert c.dry_run is True                  # LIVE DISABLED
    assert c.strategy == "top_book"
    assert c.timeframes == ("5m",)
    assert c.tb_size == 5.0
    assert c.tb_naked_cap == 10.0
    assert c.per_window_cap == 40.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_run_control_cfg.py -q` → FAIL.

- [ ] **Step 3: Implement**

`trading_state.py`: add `merged_today: float = 0.0` and `redeemed_today: float = 0.0` (same style as existing counters) and add both to the `snapshot()` dict.

`run_control.py`: add a branch (same shape as five_min):

```python
if STRATEGY == "top_book":
    CFG = Config(strategy="top_book", assets=("BTC",), timeframes=("5m",),
        tb_size=5.0, tb_naked_cap=10.0, tb_tick=0.001, tb_merge_min=5.0,
        per_window_cap=40.0, per_market_cap_usd=40.0, min_time_to_expiry_sec=5.0,
        dry_run=True)
elif STRATEGY == "five_min":
    ...
```

Keep the existing `assert CFG.dry_run is True` at the bottom untouched.

- [ ] **Step 4: Run to verify**

Run: `.venv/bin/python -m pytest tests/test_run_control_cfg.py tests/test_config_top_book.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add quoter/runner/trading_state.py quoter/runner/run_control.py tests/test_run_control_cfg.py
git commit -m "feat(topbook): STRATEGY=top_book run mode (dry-run locked) + merge/redeem metrics"
```

---

### Task 5: On-chain spike — `positions_ops` (merge/redeem via proxy)

**Files:**
- Create: `quoter/chain/__init__.py` (empty), `quoter/chain/positions_ops.py`
- Create: `docs/superpowers/specs/2026-07-02-positions-ops-spike-notes.md` (findings)

This is an INVESTIGATION task with a concrete deliverable. The wallet is a Polymarket
PROXY (env: `POLY_FUNDER_ADDRESS`, `POLY_SIGNATURE_TYPE`, EOA key `POLY_PRIVATE_KEY`).

- [ ] **Step 1: Investigate Path A (gasless relayer, preferred)**
  1. Inspect the installed `py_clob_client_v2` package for relayer/proxy helpers (grep for
     `relayer`, `proxy`, `merge`).
  2. Web-search: "polymarket relayer mergePositions api", "polymarket proxy wallet merge
     gasless github", check `github.com/Polymarket` repos (`relayer-client`, `proxy-factories`).
  3. Identify the endpoint + payload the UI uses for Merge (browser devtools capture is the
     reliable route — document it in the notes file for the operator to капture if needed).

- [ ] **Step 2: Investigate Path B (direct on-chain fallback)**
  1. `.venv/bin/pip install web3` (record version in notes).
  2. Contracts (VERIFY each address on polygonscan before use — do not trust from memory):
     ConditionalTokens (CTF) on Polygon `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`;
     USDC collateral `0x2791bca1f2de4661ed88a30c99a7a9449aa84174`; the proxy-wallet contract
     type per `POLY_SIGNATURE_TYPE` (1 = Polymarket proxy, 2 = Gnosis Safe).
  3. The call: proxy executes `CTF.mergePositions(USDC, 0x0, conditionId, [1,2], qty_1e6)`;
     redeem = `CTF.redeemPositions(USDC, 0x0, conditionId, [1,2])`. EOA needs ~$1-2 POL gas.

- [ ] **Step 3: Implement `positions_ops.py`** with the chosen path:

```python
"""On-chain position ops for the proxy wallet: merge matched pairs -> USDC, redeem resolved.
Chosen path documented in docs/superpowers/specs/2026-07-02-positions-ops-spike-notes.md.
NEVER places orders. qty bounded by held inventory by the caller."""

def merge_pairs(condition_id: str, qty: float) -> bool: ...
def redeem(condition_id: str) -> bool: ...
```

(Real bodies per the chosen path; keep the module import-safe when web3 is absent if Path A won.)

- [ ] **Step 4: Acceptance (GATED — ask the operator first)**
  STOP and ask the user for ok. On ok: buy nothing — use an EXISTING matched pair if the
  wallet holds one, else the operator manually buys a ~$1 pair via UI; then run
  `merge_pairs(cond, 1)` on the server; verify +$1 USDC on balance and position gone.
  Record tx hash in the notes file. If no ok yet: mark the task DONE_WITH_CONCERNS
  (implementation complete, acceptance pending operator).

- [ ] **Step 5: Commit**

```bash
git add quoter/chain/ docs/superpowers/specs/2026-07-02-positions-ops-spike-notes.md
git commit -m "feat(chain): positions_ops merge/redeem via proxy (spike: <path chosen>)"
```

---

### Task 6: Redeem sweeper

**Files:**
- Modify: `quoter/runner/merge_runner.py` (background coroutine, started with the runner)

- [ ] **Step 1: Add the sweeper** (isolated: its failure never stops quoting):

```python
    async def _redeem_sweeper(self) -> None:
        """Every 60s: redeem resolved positions so capital returns to cash. Dry-run: log only."""
        while not self._shutdown:
            try:
                async with httpx.AsyncClient(timeout=10) as cl:
                    r = await cl.get("https://data-api.polymarket.com/positions",
                                     params={"user": self.creds.funder, "redeemable": "true",
                                             "sizeThreshold": 1, "limit": 100})
                    for p in (r.json() if r.status_code == 200 else []):
                        if self.cfg.dry_run:
                            log.info("dryrun_redeem", slug=p.get("slug"), size=p.get("size"))
                            continue
                        from quoter.chain.positions_ops import redeem
                        if redeem(p.get("conditionId")):
                            self.state.redeemed_today += float(p.get("size", 0))
            except Exception as e:
                log.warning("redeem_sweeper_err", error=str(e))
            await asyncio.sleep(60)
```

Start it where the runner launches background tasks (next to the Binance task):
`asyncio.create_task(self._redeem_sweeper())` guarded to run only when `cfg.strategy == "top_book"`.

- [ ] **Step 2: Suite green**

Run: `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 3: Commit**

```bash
git add quoter/runner/merge_runner.py
git commit -m "feat(topbook): redeem sweeper (60s, isolated from quoting loop)"
```

---

### Task 7: Dry-run verify + deploy

- [ ] **Step 1: Local dry-run smoke**

Run: `STRATEGY=top_book timeout 30 .venv/bin/python -m quoter.runner.run_control` (or import-check if the control server binds a port locally): must log the dry-run lock, discover the 5m window, and emit `topbook_enter` + `dryrun_place` intents with prices == current best bid + 0.001; zero real orders.

- [ ] **Step 2: Full suite**

Run: `.venv/bin/python -m pytest -q` → all pass (expect ~385+).

- [ ] **Step 3: Deploy to AWS** (operator commands; IP may rotate — ask if unreachable):

```bash
KEY="~/Desktop/aws keys/<ssh-key>.pem"; HOST="ubuntu@<SERVER_IP>"
git archive --format=tar master | ssh -i "$KEY" $HOST 'cd ~/poly-quoter && tar xf -'
ssh -i "$KEY" $HOST 'sudo systemctl restart poly-control && sleep 3 && systemctl is-active poly-control poly-book'
# pin the strategy:
ssh -i "$KEY" $HOST 'sudo mkdir -p /etc/systemd/system/poly-control.service.d && printf "[Service]\nEnvironment=STRATEGY=top_book\n" | sudo tee /etc/systemd/system/poly-control.service.d/strategy.conf && sudo systemctl daemon-reload && sudo systemctl restart poly-control'
ssh -i "$KEY" $HOST 'cd ~/poly-quoter && .venv/bin/python -m pytest -q 2>&1 | tail -1'
```

Verify: dashboard status shows STOPPED (no auto-start), server tests green, `poly-book` still active.

- [ ] **Step 4: Commit any deploy fixups; report ready-for-go checklist** (spike acceptance state, dry-run intents seen, metrics visible). The live test itself is NOT part of this plan — operator-gated.

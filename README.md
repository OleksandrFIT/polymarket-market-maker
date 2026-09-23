# poly-quoter

An asynchronous **market-making bot for Polymarket's CLOB**, targeting the short-horizon
**crypto "Up or Down"** markets (BTC/ETH 5-minute and 15-minute windows). It continuously
quotes both sides of a binary market, manages inventory and risk in real time, and books
the maker spread while staying delta-aware as each window resolves.

The project is built for **latency-sensitive, high-churn quoting**: sub-second requote
cadence, a locally maintained order book fed from websockets, tick-aligned pricing, and a
regime gate that pulls quotes when the underlying starts trending instead of chopping.

> **Status:** research/paper-trading project. `shadow` and `paper` modes are complete and
> extensively tested; `live` execution is wired and has been run on-chain in short,
> supervised sessions (see [Live results](#live-results)). This is a personal R&D codebase,
> not financial advice and not a turnkey money printer.

---

## Why it's interesting

Short-horizon prediction markets are a brutal environment for a maker: spreads are thin,
the "loser" leg decays sub-tick near resolution, and a naked inventory imbalance is a
directional bet you never meant to make. The bot is an attempt to earn the spread *anyway*,
by combining:

- **A fair-value reference** from the underlying (Binance / Chainlink) instead of quoting
  blindly around the mid.
- **Inventory-aware laddering** — quotes skew to unwind imbalance rather than accumulate a
  naked leg.
- **A regime gate** — in trending ("chop-off") regimes the maker edge inverts, so the bot
  stands down instead of feeding a directional move.
- **Endgame discipline** — near resolution the losing leg goes sub-tick; the bot
  tick-aligns or skips instead of spamming invalid orders.

Much of the repository is the *research* that produced those rules: backtests, forensic
PnL splits, cadence A/Bs, and live-run post-mortems that are honest about what did **not**
work.

---

## Architecture

```
quoter/
├── main.py              # entrypoint — picks executor by MODE (shadow | paper | live)
├── quoter_loop.py       # the async quoting loop
├── config.py            # env-driven configuration
├── creds.py             # Polymarket API / signing credentials (from env, never committed)
│
├── feeds/               # market data
│   ├── binance_ws.py        # underlying spot price (fair-value reference)
│   ├── poly_market_ws.py    # Polymarket CLOB order-book stream
│   └── poly_user_ws.py      # own-order / fill stream
│
├── book/                # locally reconstructed order book
├── strategy/            # inventory model + ladder quoting
├── risk/                # position caps / RiskGuard
│
├── runner/              # the planners that turn state → orders
│   ├── top_book_planner.py    # top-of-book quoting + tick alignment
│   ├── five_min_planner.py    # 5-minute window logic
│   ├── ladder_planner.py      # multi-level ladder
│   ├── requote_planner.py     # requote / cancel-replace cadence
│   ├── flatten_planner.py     # end-of-window flattening
│   ├── regime_gate.py         # chop-vs-trend gate (stand down when trending)
│   ├── tilt_planner.py        # inventory-skew quoting
│   └── merge_runner.py        # position merge / redemption
│
├── execution/           # shadow / paper / live executors + CLOB client
├── lifecycle/           # per-window market lifecycle
├── persistence/         # SQLite state (aiosqlite)
├── ops/                 # structured logging + Prometheus metrics
├── analysis/            # PnL / behaviour analysis
├── backtest/            # simulation harness
└── research/            # strategy research modules

scripts/                 # standalone research: backtests, forensic PnL, cadence A/Bs
tests/                   # 70+ test modules (pytest-asyncio)
docs/                    # deploy runbooks + live-run post-mortems
```

### Execution modes

| MODE     | Executor          | Behaviour                                                        |
|----------|-------------------|-----------------------------------------------------------------|
| `shadow` | `ShadowExecutor`  | Computes quotes, places nothing — logs the diff vs. the book.   |
| `paper`  | `PaperExecutor`   | Simulated fills via book traversal (queue position, latency, taker size), inventory + SQLite persistence. |
| `live`   | `LiveExecutor`    | Real signed orders on the Polymarket CLOB (supervised only).    |

---

## Tech stack

- **Python 3.12**, fully `async` on **uvloop**
- **websockets** + **httpx[http2]** for feeds and REST
- **py-clob-client-v2** for Polymarket order signing / submission
- **aiosqlite** for state persistence
- **structlog** for structured logs, **Prometheus** metrics endpoint
- **pytest / pytest-asyncio**, **ruff**, **mypy --strict** for quality

---

## Running it

Requires [`uv`](https://github.com/astral-sh/uv).

```bash
# install deps
uv sync

# configure — copy the template and fill in your own values
cp .env.example .env      # then edit .env

# safest first: shadow mode (places no orders)
MODE=shadow uv run python -m quoter.main

# simulated fills
MODE=paper uv run python -m quoter.main

# tests
uv run pytest
```

`.env` (never committed) holds Polymarket credentials and run settings:

```dotenv
POLY_PRIVATE_KEY=0x...        # signing key for the trading wallet
POLY_API_KEY=...
POLY_API_SECRET=...
POLY_API_PASSPHRASE=...
POLY_FUNDER_ADDRESS=0x...
POLY_SIGNATURE_TYPE=1
MODE=paper
BANKROLL=100
LOG_LEVEL=INFO
```

> ⚠️ Use a **dedicated wallet** funded with only what you can lose. Never commit `.env`.

---

## Live results

Live trading was run in **short, supervised sessions** on real Polymarket 5-minute markets,
on a small bankroll (~$100). This is a research project, so the numbers are reported plainly —
the losing runs taught more than the winning ones.

- **Best week:** **≈ +$150** over a week of running.
- **Worst stretch:** **≈ −$9.5** over 2 days.
- **First live run** (`docs/superpowers/2026-07-16-live-run-1-postmortem.md`) is a full
  post-mortem: the sample size was too small to conclude edge, and a bug in the sell-loser
  branch (sub-tick prices → `invalid maker amount` rejections) was diagnosed offline and
  fixed with tick alignment.

**Honest takeaways**

- The maker edge in these markets is **thin and regime-dependent** — it exists in choppy
  windows and inverts in trending ones, which is exactly what the regime gate targets.
- The dominant loss source is **carrying a naked losing leg into resolution**, not
  execution mechanics. The real lever is quoting less imbalanced upstream, not recovering
  the leg after the fact.
- Solid execution plumbing (tick alignment, a watchdog, per-window flattening, structured
  telemetry) is necessary but **not sufficient** for a profitable strategy.

---

## What this codebase demonstrates

- Real-time **async systems** design: multiplexed websocket feeds, a locally reconstructed
  order book, and a sub-second quoting loop under `uvloop`.
- **Quant / market-microstructure** thinking: fair value, inventory skew, adverse-selection
  and regime awareness, endgame tick mechanics.
- **Disciplined engineering**: strict typing, 70+ async tests, a shadow→paper→live safety
  ladder, deploy runbooks, and research documented as post-mortems rather than hype.

---

## Disclaimer

Personal research and educational code. Trading prediction markets carries real financial
risk. Nothing here is financial advice. Run at your own risk, with money you can afford to
lose.

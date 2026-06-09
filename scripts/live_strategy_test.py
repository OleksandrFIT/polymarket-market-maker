"""LIVE two-sided merge-maker micro-test — does the strategy catch a pair < $1?

Uses the REAL strategy code (compute_ladder) to generate both-leg bids, posts
them via the bot's ClobOps (V2), then POLLS for fills (no WS dependency). Reports
whether we caught a matched Up+Down pair for < $1.00. Cancels leftovers at the end.

HARD CAP via per_market_cap_usd; one market only. No secrets printed.

  .venv/bin/python scripts/live_strategy_test.py          # DRY (plan only)
  .venv/bin/python scripts/live_strategy_test.py --live    # post both legs + watch
"""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import httpx
from quoter.creds import PolyCreds
from quoter.config import Config
from quoter.markets import discover_markets
from quoter.strategy.ladder import compute_ladder
from quoter.execution.clob_client import ClobOps
from py_clob_client_v2 import ClobClient, ApiCreds, BalanceAllowanceParams, AssetType

LIVE = "--live" in sys.argv
WATCH_SEC = 120

CFG = Config(
    merge_edge=0.01, max_naked_shares=10, merge_levels=1,
    flat_size=5, per_market_cap_usd=5.0, min_time_to_expiry_sec=5.0,
)


def _mid(book_y, book_n):
    def best(b, s):
        ps = [float(l["price"]) for l in (b.get(s) or [])]
        return (max(ps) if s == "bids" else min(ps)) if ps else None
    yb, ya = best(book_y, "bids"), best(book_y, "asks")
    if yb and ya:
        return (yb + ya) / 2
    nb, na = best(book_n, "bids"), best(book_n, "asks")
    if nb and na:
        return 1 - (nb + na) / 2
    return None


FRESH_MIN_SEC = 230   # only enter a window that JUST opened (>= this many sec left)
BALANCED = (0.35, 0.65)  # and whose mid is still balanced (fresh start)


async def _wait_for_fresh_window():
    """Skip the current (possibly dying/imbalanced) window; wait for the next
    fresh one that just opened with a balanced mid, then return (market, mid)."""
    async with httpx.AsyncClient(timeout=8) as cl:
        announced = False
        while True:
            mk = await discover_markets(Config(assets=("BTC",), timeframes=("5m",)), min_time_remaining_sec=10)
            if mk:
                m = mk[0]
                tte = m.time_remaining()
                by = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
                bn = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
                mid = _mid(by, bn)
                if mid and tte >= FRESH_MIN_SEC and BALANCED[0] <= mid <= BALANCED[1]:
                    return m, mid
                if not announced:
                    print(f"  waiting for a FRESH balanced window (now: {int(tte)}s left, mid≈{mid})…")
                    announced = True
            await asyncio.sleep(10)


async def main():
    print(f"=== LIVE STRATEGY MICRO-TEST ({'LIVE' if LIVE else 'DRY'}) — cap ${CFG.per_market_cap_usd:.2f} ===")
    print("policy: SKIP current window, enter only a fresh balanced one from its start.\n")
    m, mid = await _wait_for_fresh_window()
    tte = m.time_remaining()

    quotes = compute_ladder(CFG, mid, tte, inventory_yes_qty=0, inventory_no_qty=0,
                            inventory_yes_cost=0.0, inventory_no_cost=0.0)
    print(f"market: {m.slug}  ({int(tte)}s left)   mid_yes≈{mid:.3f}")
    print("strategy wants to post:")
    pair = 0.0
    for q in quotes:
        print(f"   {q.side:3} {q.size} @ ${q.price:.2f}")
        pair += q.price
    print(f"pair cost (sum of leg prices): ${pair:.3f}  ({'< $1 ✅ edge' if pair < 1 else '>= $1'})")
    if not quotes:
        print("no quotes (mid out of band?), retry"); return
    if not LIVE:
        print("\nDRY: nothing posted. Re-run with --live."); return

    c = PolyCreds.from_env()
    clob = ClobOps(c)
    rc = ClobClient("https://clob.polymarket.com", 137, key=c.private_key,
        creds=ApiCreds(api_key=c.api_key, api_secret=c.api_secret, api_passphrase=c.api_passphrase),
        signature_type=c.sig_type, funder=c.funder or None)

    def shares(tok):
        return int(rc.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL, token_id=tok, signature_type=c.sig_type)).get("balance", 0)) // 1_000_000

    print("\n[place] posting both legs (maker, post_only)…")
    oids = []
    for q in quotes:
        tok = m.yes_token if q.side == "YES" else m.no_token
        r = await clob.place_limit(token_id=tok, price=q.price, size=q.size, side="BUY", post_only=True)
        print(f"   {q.side} @ {q.price}: {r}")
        if r and r.get("order_id"):
            oids.append(r["order_id"])

    print(f"\n[watch] polling fills for up to {WATCH_SEC}s…")
    for i in range(WATCH_SEC // 10):
        await asyncio.sleep(10)
        yq, nq = shares(m.yes_token), shares(m.no_token)
        matched = min(yq, nq)
        print(f"  +{(i+1)*10}s  Up held={yq}  Down held={nq}  matched pairs={matched}")
        if yq > 0 and nq > 0:
            print(f"\n  🎯 CAUGHT A PAIR: {matched} matched Up+Down for < $1 each → locked spread!")

    print("\n[cleanup] cancelling any unfilled legs…")
    if oids:
        print("  cancelled:", await clob.cancel_orders(oids))
    yq, nq = shares(m.yes_token), shares(m.no_token)
    print(f"\n=== FINAL: Up held={yq}  Down held={nq}  matched={min(yq,nq)}  naked={abs(yq-nq)} ===")
    print("matched pairs -> hold to resolution (one pays $1) ; naked -> directional. Redeem winners on the site.")


if __name__ == "__main__":
    asyncio.run(main())

"""LIVE maker fill test — answers TWO questions cheaply (<= $2.50):
  1) Are fills captured (position shows up)?
  2) What is the REAL maker fee? (metadata says 1000bps=10%; research says 0.)

Places a resting post_only BUY (maker) on the cheaper side (<= $0.50 so 5 shares
fit the cap), waits for a taker to hit it, then measures actual USDC spent vs
shares*price to reveal the true maker fee. Cancels if unfilled. No secrets.

  .venv/bin/python scripts/live_fill_test.py            # DRY (plan only)
  .venv/bin/python scripts/live_fill_test.py --live     # place maker bid + measure
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
from py_clob_client_v2 import (
    ClobClient, OrderArgsV2, OrderType, Side, ApiCreds,
    BalanceAllowanceParams, AssetType,
)

LIVE = "--live" in sys.argv
MAX_USD = 2.50
SIZE = 5            # Polymarket minimum_order_size
WAIT_SEC = 90      # how long to wait for a taker to hit our maker bid


def _best(book, side):
    levels = book.get(side) or []
    ps = [float(l["price"]) for l in levels]
    return (max(ps) if side == "bids" else min(ps)) if ps else None


async def main():
    print(f"=== LIVE MAKER FILL TEST ({'LIVE' if LIVE else 'DRY — plan only'}) — cap ${MAX_USD:.2f} ===\n")
    mk = await discover_markets(Config(assets=("BTC", "ETH"), timeframes=("5m",)), min_time_remaining_sec=max(WAIT_SEC + 20, 60))
    if not mk:
        print("no live market with enough time left, retry shortly"); return
    m = mk[0]
    async with httpx.AsyncClient(timeout=8) as cl:
        yb = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.yes_token})).json()
        nb = (await cl.get("https://clob.polymarket.com/book", params={"token_id": m.no_token})).json()
    yb_bid, nb_bid = _best(yb, "bids"), _best(nb, "bids")
    if not yb_bid or not nb_bid:
        print("incomplete book, retry"); return

    # cheaper side (its bid <= 0.50) so 5 shares fit the cap
    if yb_bid <= nb_bid:
        side_name, tok, bid = "YES (Up)", m.yes_token, yb_bid
    else:
        side_name, tok, bid = "NO (Down)", m.no_token, nb_bid
    cost = SIZE * bid
    print(f"market : {m.slug}  ({int(m.time_remaining())}s left)")
    print(f"cheaper side: {side_name}  best_bid={bid}")
    print(f"plan   : MAKER BUY {SIZE} {side_name} @ ${bid:.2f}  (= ${cost:.2f})  post_only, wait {WAIT_SEC}s")
    if cost > MAX_USD:
        print(f"  ${cost:.2f} > cap ${MAX_USD:.2f} — both sides too pricey this window, retry."); return
    if not LIVE:
        print("\nDRY: nothing placed. Re-run with --live."); return

    c = PolyCreds.from_env()
    client = ClobClient("https://clob.polymarket.com", 137, key=c.private_key,
        creds=ApiCreds(api_key=c.api_key, api_secret=c.api_secret, api_passphrase=c.api_passphrase),
        signature_type=c.sig_type, funder=c.funder or None)

    def collateral():
        ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=c.sig_type))
        return int(ba.get("balance", 0)) / 1_000_000
    def shares():
        ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=tok, signature_type=c.sig_type))
        return int(ba.get("balance", 0))

    bal0 = collateral()
    print(f"\ncollateral before: ${bal0:.4f}   shares before: {shares()}")
    print("[place] maker post_only bid…")
    signed = client.create_order(OrderArgsV2(token_id=tok, price=round(bid, 2), size=SIZE, side=Side.BUY))
    resp = client.post_order(signed, OrderType.GTC, post_only=True)
    oid = resp.get("orderID") if isinstance(resp, dict) else None
    if not (isinstance(resp, dict) and resp.get("success") and oid):
        print("  not accepted:", str(resp)[:160]); return
    print(f"  resting. order_id {oid[:16]}…  waiting up to {WAIT_SEC}s for a taker…")

    filled = False
    for i in range(WAIT_SEC // 5):
        await asyncio.sleep(5)
        s = shares()
        if s > 0:
            bal1 = collateral()
            spent = bal0 - bal1
            expected = s * bid
            fee = spent - expected
            print(f"\n  FILLED ✅ after ~{(i+1)*5}s: hold {s} shares")
            print(f"  USDC spent: ${spent:.4f}   expected (shares*price): ${expected:.4f}")
            print(f"  >>> REAL MAKER FEE: ${fee:.4f}  ({'~0 — strategy LIVES ✅' if abs(fee) < 0.01 else 'NONZERO — investigate ❌'})")
            print(f"  market conditionId: {m.market_id}  (hold to resolution → auto_claim to redeem)")
            filled = True
            break
        print(f"  +{(i+1)*5}s: not filled yet (shares={s})")

    if not filled:
        print("\n  no taker hit our bid in time (expected from laptop / back-of-queue). Cancelling…")
        print("  cancel:", str(client.cancel_orders([oid]))[:120])
        print("  (This itself shows WHY us-east-1 matters — we sit at the back of the maker queue.)")


if __name__ == "__main__":
    asyncio.run(main())

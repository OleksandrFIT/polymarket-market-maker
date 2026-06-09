"""LIVE plumbing test on CLOB V2 (py-clob-client-v2). Place 1 tiny BUY far below
mid (post_only, won't fill), confirm accepted, cancel. No secrets printed.

  .venv/bin/python scripts/live_v2_test.py            # dry (build only)
  .venv/bin/python scripts/live_v2_test.py --live     # place + cancel
"""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from quoter.creds import PolyCreds
from quoter.config import Config
from quoter.markets import discover_markets

from py_clob_client_v2 import (
    ClobClient, OrderArgsV2, OrderType, Side, ApiCreds,
    BalanceAllowanceParams, AssetType,
)

LIVE = "--live" in sys.argv
PRICE, SIZE = 0.10, 10  # $1.00, far below ~0.5 mid


async def main():
    print(f"=== CLOB V2 PLUMBING TEST ({'LIVE' if LIVE else 'DRY — build only'}) ===\n")
    mk = await discover_markets(Config(assets=("BTC", "ETH"), timeframes=("5m",)), min_time_remaining_sec=40)
    if not mk:
        print("no live market right now, retry"); return
    m = mk[0]
    tok = m.yes_token
    print(f"market: {m.slug}  ({int(m.time_remaining())}s left)")

    c = PolyCreds.from_env()
    client = ClobClient(
        "https://clob.polymarket.com", 137, key=c.private_key,
        creds=ApiCreds(api_key=c.api_key, api_secret=c.api_secret, api_passphrase=c.api_passphrase),
        signature_type=c.sig_type, funder=c.funder or None,
    )

    # V2 collateral balance
    try:
        ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=c.sig_type))
        print("collateral balance:", ba.get("balance"), "(raw)")
    except Exception as e:
        print("balance err:", str(e)[:160])

    print(f"\nPlanned: BUY {SIZE} @ ${PRICE:.2f} (= ${PRICE*SIZE:.2f}) post_only — far below mid, won't fill.")
    print("[build] create_order (V2, EIP-712 v2, no feeRateBps)…")
    try:
        signed = client.create_order(OrderArgsV2(token_id=tok, price=PRICE, size=SIZE, side=Side.BUY))
        print("  built OK.")
    except Exception as e:
        print("  create_order ERR:", str(e)[:240]); return

    if not LIVE:
        print("\nDRY: built fine, nothing posted. Re-run with --live."); return

    print("[1/3] post_order (post_only)…")
    try:
        resp = client.post_order(signed, OrderType.GTC, post_only=True)
        print("  RESP:", str(resp)[:300])
    except Exception as e:
        print("  post_order ERR:", str(e)[:300]); return

    oid = resp.get("orderID") or resp.get("orderId") if isinstance(resp, dict) else None
    if not oid:
        print("  not accepted (no order id)."); return
    print("  ACCEPTED. order_id:", oid[:18], "…")

    print("[2/3] cancel…")
    try:
        print("  cancel:", str(client.cancel(oid))[:150])
    except Exception as e:
        print("  cancel ERR:", str(e)[:150])

    print("\n=== RESULT: V2 ORDER ACCEPTED → live order path WORKS. Plumbing solved. ===")


if __name__ == "__main__":
    asyncio.run(main())

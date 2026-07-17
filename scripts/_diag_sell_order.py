"""DIAGNOSE 'invalid maker amount' from live run #1 — OFFLINE, NO TRADING.

Live run #1 (2026-07-16) had 26/29 SELL-loser FOKs rejected with 400 'invalid maker amount'. The
order params were not logged then (fixed since), so this reconstructs the failing case by BUILDING
the orders locally (client.create_order signs but does NOT send) across a grid of (size, price) and
prints the computed makerAmount / takerAmount. A malformed amount (0, sub-tick, precision) shows up
here without any post_order — i.e. without placing a real order.

SAFE: create_order only signs locally (may do a read-only GET for tick/neg_risk). It NEVER trades.
There is no post_order call anywhere in this script.

Run on the server (needs creds + py_clob_client_v2):
  cd ~/poly-quoter && .venv/bin/python scripts/_diag_sell_order.py <token_id>
`token_id` = a real BTC 5m token (e.g. the one that failed: 86426388176372...). If omitted, the
script discovers a current BTC 5m market and uses its Down token.
"""
import os
import sys
import json


def _load_env():
    from dotenv import dotenv_values
    return dotenv_values("/home/ubuntu/poly-quoter/.env")


def main():
    env = _load_env()
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds
    try:
        from py_clob_client_v2 import OrderArgsV2, Side
    except ImportError:
        from py_clob_client_v2.clob_types import OrderArgsV2  # fallback name
        from py_clob_client_v2 import Side

    c = ClobClient(
        "https://clob.polymarket.com", chain_id=137,
        key=env["POLY_PRIVATE_KEY"],
        creds=ApiCreds(env["POLY_API_KEY"], env["POLY_API_SECRET"], env["POLY_API_PASSPHRASE"]),
        signature_type=int(env.get("POLY_SIGNATURE_TYPE", "1")),
        funder=env["POLY_FUNDER_ADDRESS"],
    )

    token = sys.argv[1] if len(sys.argv) > 1 else None
    if not token:
        # discover a live BTC 5m market, take its "Down" (No) token
        import urllib.request
        import time
        ts = (int(time.time()) // 300) * 300
        for cand in (ts, ts + 300, ts - 300):
            slug = "btc-updown-5m-%d" % cand
            try:
                u = "https://gamma-api.polymarket.com/markets?slug=%s" % slug
                m = json.load(urllib.request.urlopen(u, timeout=10))
                m = m[0] if isinstance(m, list) and m else m
                toks = json.loads(m["clobTokenIds"])
                token = toks[1]  # Down / No
                print("discovered %s -> token %s..." % (slug, token[:16]))
                break
            except Exception:
                continue
    if not token:
        print("no token; pass one as argv[1]")
        return

    print("\n=== BUILDING SELL orders (create_order only, NOT posted) ===")
    print("looking for which (size, price) yields a malformed maker/taker amount\n")
    print("%-6s %-7s | %-16s %-16s | %s" % ("size", "price", "makerAmount", "takerAmount", "build result"))
    for size in (1, 2, 3, 4, 5, 6):
        for price in (0.0, 0.001, 0.003, 0.005, 0.009, 0.01, 0.015):
            args = OrderArgsV2(token_id=token, price=float(price), size=int(size), side=Side.SELL)
            try:
                signed = c.create_order(args)
                # signed is an object/dict; pull the amounts however they are exposed
                d = signed if isinstance(signed, dict) else getattr(signed, "__dict__", {})
                order = d.get("order") or d
                ma = order.get("makerAmount") if isinstance(order, dict) else getattr(order, "makerAmount", "?")
                ta = order.get("takerAmount") if isinstance(order, dict) else getattr(order, "takerAmount", "?")
                # a valid SELL: makerAmount = shares (>0), takerAmount = shares*price (>0)
                bad = []
                try:
                    if int(ma) <= 0:
                        bad.append("makerAmount<=0")
                    if int(ta) <= 0:
                        bad.append("takerAmount<=0")
                except Exception:
                    bad.append("non-int amount")
                verdict = "MALFORMED: " + ",".join(bad) if bad else "ok"
                print("%-6d %-7.2f | %-16s %-16s | %s" % (size, price, str(ma), str(ta), verdict))
            except Exception as e:
                print("%-6d %-7.2f | %-16s %-16s | create_order RAISED: %s" % (
                    size, price, "-", "-", str(e)[:70]))

    print("\n=== market rules (gamma) for this token's market ===")
    try:
        import urllib.request
        # token -> market via clob
        info = c.get_market(token) if hasattr(c, "get_market") else {}
        for k in ("minimum_order_size", "minimum_tick_size", "neg_risk"):
            if isinstance(info, dict) and k in info:
                print("  %-22s = %s" % (k, info[k]))
    except Exception as e:
        print("  (market lookup failed: %s)" % str(e)[:80])


if __name__ == "__main__":
    main()

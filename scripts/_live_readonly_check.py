"""READ-ONLY live account check. Places/cancels NOTHING. Prints no secrets."""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from quoter.creds import PolyCreds

def mask(s): return (s[:6] + "…" + s[-4:]) if s and len(s) > 10 else "(set)"

try:
    c = PolyCreds.from_env()
    print("creds loaded: YES (all 4 keys present)")
    print(f"  signature_type: {c.sig_type}")
    print(f"  funder (proxy) address: {mask(c.funder)}")
except Exception as e:
    print("creds loaded: NO ->", e); raise SystemExit

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

client = ClobClient(
    host="https://clob.polymarket.com", key=c.private_key, chain_id=137,
    creds=ApiCreds(api_key=c.api_key, api_secret=c.api_secret, api_passphrase=c.api_passphrase),
    signature_type=c.sig_type, funder=c.funder or None,
)

# READ-ONLY 1: USDC balance + allowance
try:
    ba = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    raw = float(ba.get("balance", 0))
    usdc = raw / 1_000_000  # USDC has 6 decimals
    allow = float(ba.get("allowance", 0)) / 1_000_000 if ba.get("allowance") else None
    print(f"\nUSDC balance: ${usdc:.4f}")
    print(f"USDC allowance to CLOB: " + (f"${allow:.2f}" if allow is not None else "(n/a in response)"))
except Exception as e:
    print(f"\nbalance check FAILED -> {type(e).__name__}: {str(e)[:200]}")

# READ-ONLY 2: open orders
try:
    orders = client.get_orders()
    n = len(orders.get("data", [])) if isinstance(orders, dict) else len(orders or [])
    print(f"open orders right now: {n}")
except Exception as e:
    print(f"open-orders check FAILED -> {type(e).__name__}: {str(e)[:200]}")

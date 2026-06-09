"""RED STOP — cancel ALL open orders on the account immediately. No secrets."""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from quoter.creds import PolyCreds
from py_clob_client_v2 import ClobClient, ApiCreds

c = PolyCreds.from_env()
cl = ClobClient("https://clob.polymarket.com", 137, key=c.private_key,
    creds=ApiCreds(api_key=c.api_key, api_secret=c.api_secret, api_passphrase=c.api_passphrase),
    signature_type=c.sig_type, funder=c.funder or None)

before = cl.get_open_orders() or []
print(f"open orders before: {len(before)}")
print("cancel_all ->", cl.cancel_all())
after = cl.get_open_orders() or []
print(f"open orders after:  {len(after)}  ({'CLEAN ✅' if not after else 'still some — retry'})")

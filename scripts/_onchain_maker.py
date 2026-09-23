"""On-chain MAKER fills for a wallet (data-api /trades can't see makers).
Query Polygon OrderFilled events (CTFExchange + NegRiskCtfExchange) where
maker == addr, decode side/price/size, map token->market via gamma, compute
minute-in-window from block timestamp. Reveals the maker's real posting tactic.
Usage: python3 scripts/_onchain_maker.py [addr] [lookback_blocks]
"""
import urllib.request, json, sys, time, collections
from eth_utils import keccak

ADDR = (sys.argv[1] if len(sys.argv) > 1 else "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82").lower()
LOOKBACK = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
RPCS = ["https://polygon.drpc.org", "https://1rpc.io/matic"]
EXCH = ["0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",   # CTFExchange
        "0xc5d563a36ae78145c45a50134d48a1215220f80a"]   # NegRiskCtfExchange
TOPIC0 = "0x" + keccak(text="OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)").hex()
MAKER_TOPIC = "0x" + "0" * 24 + ADDR[2:]
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for url in RPCS:
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, data=body, headers=UA), timeout=30))
            if "result" in r:
                return r["result"]
        except Exception:
            continue
    return None


def hx(x):
    return hex(x) if isinstance(x, int) else x


head = int(rpc("eth_blockNumber", []), 16)
frm = head - LOOKBACK
print("head block %d, scanning %d..%d (~%.1f min)\n" % (head, frm, head, LOOKBACK * 2 / 60))

logs = []
for ex in EXCH:
    step = 1000
    b = frm
    while b <= head:
        chunk = rpc("eth_getLogs", [{"fromBlock": hx(b), "toBlock": hx(min(b + step - 1, head)),
                                     "address": ex, "topics": [TOPIC0, None, MAKER_TOPIC]}])
        if isinstance(chunk, list):
            logs += [(ex, lg) for lg in chunk]
        b += step
print("OrderFilled logs with maker=%s: %d" % (ADDR[:10], len(logs)))
if not logs:
    print("no maker fills in range (try larger lookback)"); sys.exit()

# block timestamps (batch unique)
blocks = sorted({int(lg["blockNumber"], 16) for _, lg in logs})
bts = {}
for bn in blocks:
    r = rpc("eth_getBlockByNumber", [hex(bn), False])
    if r:
        bts[bn] = int(r["timestamp"], 16)
    time.sleep(0.02)

# token id -> (slug, open_ts, side) via gamma clobTokenIds lookup (cached)
tokmap = {}
def resolve_token(tid):
    if tid in tokmap:
        return tokmap[tid]
    r = None
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(
            "https://gamma-api.polymarket.com/markets?clob_token_ids=%s" % tid, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
    except Exception:
        r = None
    out = None
    if isinstance(r, list) and r:
        m = r[0]; slug = m.get("slug", "")
        try:
            toks = json.loads(m.get("clobTokenIds", "[]"))
            side = "Up" if str(tid) == str(toks[0]) else "Dn"
        except Exception:
            side = "?"
        try:
            open_ts = int(slug.rsplit("-", 1)[1])
        except Exception:
            open_ts = 0
        out = (slug, open_ts, side)
    tokmap[tid] = out
    return out

def d(data, i):  # i-th uint256 from data hex
    data = data[2:]
    return int(data[i * 64:(i + 1) * 64], 16)

fills = []
for ex, lg in logs:
    data = lg["data"]
    makerAssetId = d(data, 0); takerAssetId = d(data, 1)
    makerAmt = d(data, 2) / 1e6; takerAmt = d(data, 3) / 1e6
    if makerAssetId == 0:      # maker pays USDC -> BUY takerAsset
        act = "BUY"; tid = takerAssetId; price = makerAmt / takerAmt if takerAmt else 0; size = takerAmt
    else:                      # maker gives token -> SELL makerAsset
        act = "SELL"; tid = makerAssetId; price = takerAmt / makerAmt if makerAmt else 0; size = makerAmt
    bn = int(lg["blockNumber"], 16); ts = bts.get(bn, 0)
    fills.append({"act": act, "tid": str(tid), "price": price, "size": size, "ts": ts})

print("decoded fills: %d\n" % len(fills))
# side/price/timing (resolve a sample of tokens to keep gamma calls bounded)
acts = collections.Counter(f["act"] for f in fills)
print("actions:", dict(acts))
buys = [f["price"] for f in fills if f["act"] == "BUY"]
print("BUY price: median %.3f  n=%d" % (sorted(buys)[len(buys)//2] if buys else 0, len(buys)))
pbuck = collections.Counter(min(int(p*10)/10, 0.9) for p in buys)
print("\nMAKER BUY price histogram (on-chain, real fills):")
for b in sorted(pbuck):
    print("  %.1f-%.1f  %4d  %s" % (b, b+0.1, pbuck[b], "#"*int(40*pbuck[b]/max(1,len(buys)))))

# timing: resolve tokens for a sample to get open_ts
print("\nresolving tokens for minute-in-window (sample <=120)...")
sample = fills[:120]
mins = []
for f in sample:
    info = resolve_token(f["tid"])
    if info and info[1] and f["ts"]:
        mins.append((f["ts"] - info[1]) / 60.0)
if mins:
    mb = collections.Counter(max(0, min(14, int(m))) for m in mins)
    print("maker-fill minute-in-window (sampled %d):" % len(mins))
    for b in sorted(mb):
        print("  min %2d-%2d  %4d  %s" % (b, b+1, mb[b], "#"*int(40*mb[b]/max(1,len(mins)))))

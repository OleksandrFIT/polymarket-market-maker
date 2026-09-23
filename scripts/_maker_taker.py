"""Maker vs Taker classification for wallet 0xb945...db68 (24h).
No explicit field in data-api -> infer from fill price vs contemporaneous mid:
buy below mid = MAKER (resting bid hit), above mid = TAKER (lifted ask). Read-only.
"""
import urllib.request, json, time, collections, statistics
W="0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
BASE="https://data-api.polymarket.com"; UA={"User-Agent":"Mozilla/5.0"}
NOW=int(time.time()); SINCE=NOW-24*3600; EPS=0.02
def get(u,tries=3):
    for k in range(tries):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30))
        except Exception:
            if k==tries-1: return None
            time.sleep(0.4)
# fetch trades
trades=[]; off=0
while True:
    b=get("%s/trades?user=%s&limit=500&offset=%d"%(BASE,W,off))
    if not b: break
    keep=[t for t in b if t.get("timestamp",0)>=SINCE and t.get("side")=="BUY"]
    trades+=keep
    if len([t for t in b if t.get("timestamp",0)>=SINCE])<len(b) or len(b)<500: break
    off+=500; time.sleep(0.1)
print("BUY trades 24h: %d"%len(trades))
# group by asset (token); fetch each token's 1-min price history once
bytok=collections.defaultdict(list)
for t in trades: bytok[t["asset"]].append(t)
# window open ts from slug
def open_ts(slug):
    try: return int(slug.split("-")[-1])
    except: return None
pathcache={}
def tok_price_at(asset, slug, ts):
    if asset not in pathcache:
        ot=open_ts(slug); 
        h=[]
        if ot:
            r=get("https://clob.polymarket.com/prices-history?market=%s&startTs=%d&endTs=%d&fidelity=1"%(asset,ot-30,ot+960))
            h=r.get("history",[]) if isinstance(r,dict) else []
        pathcache[asset]=h
    h=pathcache[asset]
    if not h: return None
    # nearest point in time
    best=min(h,key=lambda p:abs(p["t"]-ts))
    return best["p"] if abs(best["t"]-ts)<=90 else None

maker=taker=atmid=unknown=0
maker_usd=taker_usd=0.0
diffs=[]
by_bucket=collections.defaultdict(lambda:[0,0,0])  # pricebucket -> [maker,taker,atmid]
n=0
for asset,ts_trades in bytok.items():
    for t in ts_trades:
        ref=tok_price_at(asset,t["slug"],t["timestamp"])
        n+=1
        his=float(t["price"]); sz=float(t["size"]); usd=his*sz
        if ref is None: unknown+=1; continue
        d=his-ref; diffs.append(d)
        b=int(his*10)/10.0
        if d < -EPS: maker+=1; maker_usd+=usd; by_bucket[b][0]+=1
        elif d > EPS: taker+=1; taker_usd+=usd; by_bucket[b][1]+=1
        else: atmid+=1; by_bucket[b][2]+=1
    time.sleep(0.01)
cl=maker+taker+atmid
print("\nclassified %d/%d trades (%d unknown/no-ref)"%(cl,n,unknown))
if cl:
    print("\n=== MAKER vs TAKER (by trade count) ===")
    print("  MAKER (buy < mid-2c): %d  (%.0f%%)"%(maker,100*maker/cl))
    print("  TAKER (buy > mid+2c): %d  (%.0f%%)"%(taker,100*taker/cl))
    print("  at-mid (±2c)        : %d  (%.0f%%)"%(atmid,100*atmid/cl))
    print("\n=== by $ volume ===")
    tot=maker_usd+taker_usd
    if tot: print("  MAKER $%.0f (%.0f%%)  |  TAKER $%.0f (%.0f%%)"%(maker_usd,100*maker_usd/tot,taker_usd,100*taker_usd/tot))
    print("\n=== price diff (his - mid) ===")
    print("  mean %+.3f  median %+.3f"%(statistics.mean(diffs),statistics.median(diffs)))
    print("\n=== maker/taker by entry-price bucket ===")
    print("  price    maker  taker  atmid")
    for b in sorted(by_bucket):
        mk,tk,am=by_bucket[b]
        print("  %.1f-%.1f  %4d   %4d   %4d"%(b,b+0.1,mk,tk,am))

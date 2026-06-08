import asyncio, time, statistics, httpx
from quoter.config import Config
from quoter.markets import discover_markets

CLOB="https://clob.polymarket.com/book"

async def book(client, tid):
    try:
        r=await client.get(CLOB, params={"token_id":tid})
        if r.status_code!=200: return None
        d=r.json()
        bids=d.get("bids") or []; asks=d.get("asks") or []
        # polymarket returns bids ascending? take best = max price bid, min price ask
        bb=max((float(b["price"]) for b in bids), default=None)
        ba=min((float(a["price"]) for a in asks), default=None)
        bbs=max(((float(b["price"]),float(b["size"])) for b in bids), default=(None,0))[1] if bids else 0
        bas=min(((float(a["price"]),float(a["size"])) for a in asks), default=(None,0))[1] if asks else 0
        return bb,ba,bbs,bas
    except Exception:
        return None

async def main():
    cfg=Config(assets=("BTC","ETH"), timeframes=("5m","15m"))
    samples=[]
    async with httpx.AsyncClient(timeout=8, headers={"User-Agent":"poly-quoter"}) as client:
        t_end=time.time()+150   # ~2.5 min
        while time.time()<t_end:
            mkts=await discover_markets(cfg, min_time_remaining_sec=20)
            for m in mkts:
                up=await book(client, m.yes_token)   # YES=Up
                dn=await book(client, m.no_token)    # NO=Down
                if not up or not dn: continue
                bb_u,ba_u,bbs_u,_=up; bb_d,ba_d,bbs_d,_=dn
                if None in (bb_u,bb_d,ba_u,ba_d): continue
                samples.append({
                    "asset":m.asset,"tf":m.timeframe,
                    "bid_pair":bb_u+bb_d, "ask_pair":ba_u+ba_d, "mid_pair":(bb_u+ba_u)/2+(bb_d+ba_d)/2,
                    "bid_u":bb_u,"bid_d":bb_d,"depth":min(bbs_u,bbs_d),
                })
            await asyncio.sleep(4)
    if not samples:
        print("NO SAMPLES (no live markets / book empty)"); return
    bp=[s["bid_pair"] for s in samples]
    ap=[s["ask_pair"] for s in samples]
    sub1=[s for s in samples if s["bid_pair"]<1.0]
    print(f"samples: {len(samples)} (across live BTC/ETH 5m+15m windows)\n")
    print("=== MAKER PAIR FEASIBILITY: best_bid_Up + best_bid_Down ===")
    bp.sort()
    print(f"  median ${statistics.median(bp):.3f}   min ${bp[0]:.3f}   max ${bp[-1]:.3f}")
    print(f"  samples with bid_pair < $1.00 (capturable spread): {len(sub1)}/{len(samples)} ({100*len(sub1)/len(samples):.0f}%)")
    if sub1:
        edges=[1.0-s["bid_pair"] for s in sub1]
        print(f"  capturable edge per pair (= $1 - bid_pair): median ${statistics.median(edges):.4f}  max ${max(edges):.4f}")
        print(f"  depth at best bid (min Up/Down size): median {statistics.median([s['depth'] for s in sub1]):.0f} shares")
    print(f"\n=== TAKER PAIR (sanity, should be >$1): ask_Up + ask_Down ===")
    ap.sort(); print(f"  median ${statistics.median(ap):.3f}   min ${ap[0]:.3f}")
    print(f"  samples with ask_pair < $1.00 (FREE arb!): {sum(1 for x in ap if x<1.0)}/{len(ap)}")
    # per asset/tf
    print(f"\n  by market:")
    from collections import defaultdict
    g=defaultdict(list)
    for s in samples: g[(s['asset'],s['tf'])].append(s['bid_pair'])
    for k,v in sorted(g.items()):
        print(f"    {k[0]} {k[1]}: median bid_pair ${statistics.median(v):.3f}  (n={len(v)})")

asyncio.run(main())

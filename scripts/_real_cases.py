import json, urllib.request, time
from collections import defaultdict
ADDR="0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
def get(u):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return json.load(urllib.request.urlopen(r,timeout=30))
seen=set(); trades=[]
for off in range(0,3500,500):
    try: b=get(f"https://data-api.polymarket.com/activity?user={ADDR}&limit=500&offset={off}&sortBy=TIMESTAMP&sortDirection=DESC")
    except: break
    if not b: break
    for t in b:
        if t.get("type")!="TRADE" or "updown" not in t.get("slug",""): continue
        h=t["transactionHash"]+str(t.get("asset"))+str(t.get("size"))
        if h in seen: continue
        seen.add(h); trades.append(t)
    if len(b)<500: break
    time.sleep(0.05)

# group by window
W=defaultdict(lambda: {"Up":[], "Down":[], "title":"", "open":0})
for t in trades:
    w=W[t["conditionId"]]
    w[t["outcome"]].append((float(t["price"]), float(t["size"]), int(t["timestamp"])))
    w["title"]=t["title"]; 
    import re; m=re.search(r"-(\d+)$",t["slug"]); w["open"]=int(m.group(1)) if m else 0

# pick clear two-sided cases with good fills, sorted by merge profit
cases=[]
for c,w in W.items():
    if not w["Up"] or not w["Down"]: continue
    us=sum(s for _,s,_ in w["Up"]); ud=sum(p*s for p,s,_ in w["Up"])
    ds=sum(s for _,s,_ in w["Down"]); dd=sum(p*s for p,s,_ in w["Down"])
    aup=ud/us; adn=dd/ds; pair=aup+adn; ms=min(us,ds)
    cases.append((ms*(1-pair), c, w, us,aup, ds,adn, pair, ms))
cases.sort(reverse=True)

def show(c, w, us,aup, ds,adn, pair, ms, prof):
    print("="*68)
    print(w["title"])
    print(f"conditionId: {c[:24]}...")
    up_t=sorted(w["Up"], key=lambda x:x[2]); dn_t=sorted(w["Down"], key=lambda x:x[2])
    def secs(ts): return ts-w["open"]
    print(f"\n  UP leg  ({len(up_t)} fills): " + ", ".join(f"{s:.0f}@{p:.2f}" for p,s,_ in up_t[:8]) + (" ..." if len(up_t)>8 else ""))
    print(f"  DOWN leg({len(dn_t)} fills): " + ", ".join(f"{s:.0f}@{p:.2f}" for p,s,_ in dn_t[:8]) + (" ..." if len(dn_t)>8 else ""))
    print(f"\n  Up:   {us:.0f} shares @ avg {aup:.3f}  = ${us*aup:,.0f}")
    print(f"  Down: {ds:.0f} shares @ avg {adn:.3f}  = ${ds*adn:,.0f}")
    print(f"  --> PAIR COST = {aup:.3f} + {adn:.3f} = ${pair:.3f}")
    print(f"  --> MERGE {ms:.0f} pairs: pay ${ms*pair:,.2f}  ->  redeem ${ms*1.0:,.2f}  =  LOCKED ${prof:,.2f}")

print(f"\nsample: {len(trades)} trades, {len(W)} windows\n")
print("########## TOP MERGE-PROFITABLE REAL WINDOWS ##########\n")
for prof,c,w,us,aup,ds,adn,pair,ms in cases[:3]:
    show(c,w,us,aup,ds,adn,pair,ms,prof)
print("\n########## A LOSING / >$1 PAIR (he doesn't always win) ##########\n")
for prof,c,w,us,aup,ds,adn,pair,ms in cases[::-1]:
    if pair>1.0:
        show(c,w,us,aup,ds,adn,pair,ms,prof); break

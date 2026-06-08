import json, urllib.request, time
from collections import defaultdict
ADDR="0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
def get(u):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return json.load(urllib.request.urlopen(r,timeout=30))
seen=set(); tr=[]
for off in range(0,3500,500):
    try: b=get(f"https://data-api.polymarket.com/activity?user={ADDR}&limit=500&offset={off}&sortBy=TIMESTAMP&sortDirection=DESC")
    except: break
    if not b: break
    for t in b:
        if t.get("type")!="TRADE" or "updown" not in t.get("slug",""): continue
        h=t["transactionHash"]+str(t.get("asset"))+str(t.get("size"))
        if h in seen: continue
        seen.add(h); tr.append(t)
    if len(b)<500: break
    time.sleep(0.05)
W=defaultdict(lambda:{"Up":[0,0.0],"Down":[0,0.0]})
for t in tr:
    w=W[t["conditionId"]][t["outcome"]]; w[0]+=float(t["size"]); w[1]+=float(t["usdcSize"])
res=[]
for c,w in W.items():
    us,_=w["Up"]; ds,_=w["Down"]
    if us==0 or ds==0: continue
    matched=min(us,ds); total=us+ds
    naked=abs(us-ds)
    res.append((matched, naked, naked/max(us,ds), us, ds))
res.sort(key=lambda x:-x[0])
print(f"two-sided windows: {len(res)}")
nk=[r[2] for r in res]; nk.sort()
print(f"naked residual = |Up-Down|/max(Up,Down):  median {nk[len(nk)//2]*100:.0f}%   p25 {nk[len(nk)//4]*100:.0f}%   p75 {nk[3*len(nk)//4]*100:.0f}%")
tot_m=sum(r[0] for r in res); tot_n=sum(r[1] for r in res)
print(f"total matched (mergeable) shares: {tot_m:,.0f}")
print(f"total naked (unhedged) shares:    {tot_n:,.0f}  ({100*tot_n/(tot_m+tot_n):.0f}% of all shares left naked)")
print(f"\nsample windows (matched / naked / Up / Down):")
for m,n,r,us,ds in res[:6]:
    print(f"  matched {m:>5.0f}  naked {n:>4.0f} ({r*100:>2.0f}%)   Up {us:>5.0f}  Down {ds:>5.0f}")

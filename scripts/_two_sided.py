import json, re, urllib.request
from collections import defaultdict
ADDR="0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
def get(u):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return json.load(urllib.request.urlopen(r,timeout=30))
trades=[]
for off in range(0,2000,500):
    b=get(f"https://data-api.polymarket.com/activity?user={ADDR}&limit=500&offset={off}&sortBy=TIMESTAMP&sortDirection=DESC")
    if not b: break
    trades+=[t for t in b if t.get("type")=="TRADE"]
# group by window (conditionId)
win=defaultdict(lambda: defaultdict(lambda:[0,0.0]))  # cond -> outcome -> [count,$]
for t in trades:
    if "updown" not in t.get("slug",""): continue
    c=t["conditionId"]; o=t["outcome"]
    win[c][o][0]+=1; win[c][o][1]+=float(t["usdcSize"])
both=0; one=0; both_dollars=0.0; hedged_dollars=0.0
for c,od in win.items():
    if len(od)>=2:
        both+=1
        up=od.get("Up",[0,0])[1]; dn=od.get("Down",[0,0])[1]
        both_dollars+=up+dn
        hedged_dollars+=2*min(up,dn)   # $ that is hedged (mergeable Up+Down pair)
    else:
        one+=1
tot=both+one
print(f"windows traded: {tot}")
print(f"  BOTH sides (Up+Down): {both} ({100*both/tot:.0f}%)")
print(f"  ONE side only:        {one} ({100*one/tot:.0f}%)")
print(f"\n$ in two-sided windows: ${both_dollars:,.0f}")
print(f"  of which HEDGED (mergeable Up+Down overlap): ${hedged_dollars:,.0f} ({100*hedged_dollars/both_dollars:.0f}%)")
# net directional skew per two-sided window
skews=[]
for c,od in win.items():
    if len(od)>=2:
        up=od.get("Up",[0,0])[1]; dn=od.get("Down",[0,0])[1]
        tot_d=up+dn
        skews.append(abs(up-dn)/tot_d if tot_d else 0)
if skews:
    skews.sort()
    print(f"\nIn two-sided windows, net directional skew |Up-Down|/total:")
    print(f"  median {skews[len(skews)//2]:.2f}  (0=perfectly hedged, 1=fully one-sided)")

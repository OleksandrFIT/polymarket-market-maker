import json, urllib.request, time
from collections import defaultdict
ADDR="0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
def get(u):
    r=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    return json.load(urllib.request.urlopen(r,timeout=30))

seen=set(); trades=[]
for off in range(0, 40000, 500):          # paginate deep
    try:
        b=get(f"https://data-api.polymarket.com/activity?user={ADDR}&limit=500&offset={off}&sortBy=TIMESTAMP&sortDirection=DESC")
    except Exception as e:
        print("stop at",off,e); break
    if not b: break
    new=0
    for t in b:
        if t.get("type")!="TRADE": continue
        h=t["transactionHash"]+str(t.get("asset"))+str(t.get("size"))
        if h in seen: continue
        seen.add(h)
        if "updown" in t.get("slug",""):
            trades.append(t); new+=1
    if len(b)<500: break
    time.sleep(0.05)

ts=[int(t["timestamp"]) for t in trades]
span_min=(max(ts)-min(ts))/60 if ts else 0
print(f"updown trades: {len(trades)}  span: {span_min:.0f} min ({span_min/60:.1f} h)")

win=defaultdict(lambda: defaultdict(lambda:[0,0.0]))  # cond -> outcome -> [shares,$]
for t in trades:
    win[t["conditionId"]][t["outcome"]][0]+=float(t["size"])
    win[t["conditionId"]][t["outcome"]][1]+=float(t["usdcSize"])

both=one=0; both_d=hedged_d=0.0
pair_costs=[]; merge_profit=0.0; merge_shares=0.0
for c,od in win.items():
    if len(od)>=2:
        both+=1
        up_s,up_d=od.get("Up",[0,0]); dn_s,dn_d=od.get("Down",[0,0])
        both_d+=up_d+dn_d; hedged_d+=2*min(up_d,dn_d)
        if up_s>0 and dn_s>0:
            avg_up=up_d/up_s; avg_dn=dn_d/dn_s
            pair=avg_up+avg_dn; pair_costs.append(pair)
            ms=min(up_s,dn_s)
            merge_profit+=ms*(1.0-pair); merge_shares+=ms
    else: one+=1
tot=both+one
print(f"\nwindows: {tot}   BOTH sides: {both} ({100*both/tot:.0f}%)   one side: {one} ({100*one/tot:.0f}%)")
print(f"$ hedged (mergeable overlap): ${hedged_d:,.0f} / ${both_d:,.0f}  ({100*hedged_d/both_d:.0f}%)")

if pair_costs:
    pair_costs.sort(); n=len(pair_costs)
    med=pair_costs[n//2]; sub1=sum(1 for p in pair_costs if p<1.0)
    print(f"\n=== MERGE EDGE (Binance-independent, from his own fills) ===")
    print(f"two-sided windows with both shares: {n}")
    print(f"pair cost (avg_Up_px + avg_Down_px):  median {med:.3f}")
    print(f"  p25 {pair_costs[n//4]:.3f}   p75 {pair_costs[3*n//4]:.3f}   min {pair_costs[0]:.3f}   max {pair_costs[-1]:.3f}")
    print(f"  windows with pair < $1.00 (merge-profitable): {sub1}/{n} ({100*sub1/n:.0f}%)")
    print(f"\nEstimated LOCKED merge profit over sample: ${merge_profit:,.0f}  on {merge_shares:,.0f} merged shares")
    print(f"  avg locked $/merged share: ${merge_profit/merge_shares:.4f}  (= 1 - pair_cost)")

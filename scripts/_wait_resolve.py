import json, urllib.request, time
ADDR="0xa0825a0a9e3b43e8f054a7d537137f626b096fbd"
COND_PREFIX="0xfa13764f"
def get(u): return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent":"curl"}), timeout=20))
for i in range(40):  # ~20 min max
    try:
        pos = get(f"https://data-api.polymarket.com/positions?user={ADDR}")
    except Exception as e:
        time.sleep(30); continue
    for p in pos:
        if str(p.get("conditionId","")).startswith(COND_PREFIX):
            sz=float(p.get("size") or 0); cur=float(p.get("curPrice") or 0); red=p.get("redeemable")
            if 4<=sz<=6 and red and cur>=0.99:
                print(f"RESOLVED ✅ WINNER: {p.get('outcome')} ({sz:.0f} shares) -> redeemable for ${sz:.2f}")
                print(f"market: {p.get('title','')}")
                raise SystemExit(0)
    print(f"  poll {i+1}: still live, waiting…", flush=True)
    time.sleep(30)
print("not resolved within 20min — check manually")

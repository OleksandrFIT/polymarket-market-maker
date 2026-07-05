#!/bin/bash
# Attended-live watchdog v2 — EQUITY-based (fix after the 2026-07-05 false stop:
# v1 compared pUSD cash only, blind to merge returns in USDC.e and to position value,
# so a normal trending window looked like a -$16 drawdown when reality was -$1.2).
# equity = pUSD + USDC.e (on-chain) + $1 x matched pairs in open positions (naked counted
# at $0 — conservative). Baseline = first reading (delete live_watch_base.txt to reset).
# Breaches -> POST /api/force_stop: equity drawdown < -$10 | naked > 8 | merge_fails >= 2.
# naked tripwire is cap (6) + 2-share slack for on-chain read lag; the hard skew gate
# should hold naked <= 6, so a read > 8 means the gate failed -> stop.
LIMIT=-10.0
BASE_F=/home/ubuntu/live_watch_base.txt
while true; do
  EQ=$(/home/ubuntu/poly-quoter/.venv/bin/python - << 'PYEOF'
import os, json, urllib.request
for line in open("/home/ubuntu/poly-quoter/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k, v)
proxy = os.environ["POLY_FUNDER_ADDRESS"]
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
def bal(token):
    data = "0x70a08231" + proxy[2:].lower().rjust(64, "0")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": token, "data": data}, "latest"]}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://polygon.drpc.org", data=body, headers=UA), timeout=15))
    return int(r["result"], 16) / 1e6
pusd = bal("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
usdce = bal("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
pairs_val = 0.0
try:
    pos = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://data-api.polymarket.com/positions?user=%s&sizeThreshold=0.5&limit=100" % proxy,
        headers={"User-Agent": "Mozilla/5.0"}), timeout=15))
    byslug = {}
    for p in pos:
        d = byslug.setdefault(p.get("slug", "?"), {})
        d[p.get("outcome", "?")] = float(p.get("size", 0))
    for d in byslug.values():
        pairs_val += min(d.get("Up", 0.0), d.get("Down", 0.0))   # $1 per matched pair
except Exception:
    pass
print("%.2f %.2f %.2f %.2f" % (pusd, usdce, pairs_val, pusd + usdce + pairs_val))
PYEOF
)
  if [ -z "$EQ" ]; then echo "$(date -u +%T) NO-EQUITY-READ"; sleep 10; continue; fi
  read -r PUSD USDCE PAIRS TOTAL <<< "$EQ"
  [ -f "$BASE_F" ] || echo "$TOTAL" > "$BASE_F"
  BASE=$(cat "$BASE_F")
  DD=$(python3 -c "print(round($TOTAL-$BASE,2))")
  S=$(curl -s -m4 http://127.0.0.1:8080/api/status)
  MODE=$(echo "$S" | python3 -c "import sys,json;print(json.load(sys.stdin).get('mode'))" 2>/dev/null)
  NAKED=$(echo "$S" | python3 -c "import sys,json;print(int(json.load(sys.stdin).get('naked_shares') or 0))" 2>/dev/null)
  MF=$(grep -c merge_failed /home/ubuntu/poly-quoter/logs/control.log 2>/dev/null)
  echo "$(date -u +%T) mode=$MODE equity=$TOTAL (pUSD=$PUSD usdce=$USDCE pairs=\$$PAIRS) dd=$DD naked=$NAKED merge_fails=$MF"
  BREACH=""
  python3 -c "exit(0 if float('$DD') < $LIMIT else 1)" && BREACH="equity dd<$LIMIT"
  [ "${NAKED:-0}" -gt 8 ] 2>/dev/null && BREACH="naked>8"
  [ "${MF:-0}" -ge 2 ] 2>/dev/null && BREACH="merge_failed x$MF"
  if [ -n "$BREACH" ] && [ "$MODE" = "RUNNING" ]; then
    echo "$(date -u +%T) *** BREACH: $BREACH -> FORCE STOP ***"
    curl -s -m5 -X POST http://127.0.0.1:8080/api/force_stop
  fi
  sleep 10
done

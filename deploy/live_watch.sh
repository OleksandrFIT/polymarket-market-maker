#!/bin/bash
# Attended-live watchdog v3. Fixes the 2026-07-06 FALSE stop: v2 counted naked at $0 and
# used a single-read equity-dd, so during active trading ($15 cash committed to not-yet-
# indexed / naked shares) the reading dipped to ~-$10 and force-stopped a window that was
# actually +$1.25. v3:
#   - equity = pUSD + USDC.e (on-chain) + MARKET value of ALL positions (size x curPrice):
#     matched pairs ~= $1, naked valued at its price (not $0) -> no static undercount.
#   - equity-dd breach must PERSIST 2 consecutive reads before force_stop (kills a transient
#     data-api positions lag dip; a real loss persists).
#   - merge_fails counted from the watchdog's OWN start (delta), not the whole historical log.
# naked>8 (instant; local count from the bot, reliable) and merge_fails>=2 still fire at once.
# Baseline = first reading (delete live_watch_base.txt to reset).
LIMIT=-10.0
DD_NEEDED=2                                   # consecutive equity-dd breaches before stop
BASE_F=/home/ubuntu/live_watch_base.txt
LOG=/home/ubuntu/poly-quoter/logs/control.log
MF_BASE=$(grep -c merge_failed "$LOG" 2>/dev/null || echo 0)   # merges failed BEFORE we started
DD_COUNT=0
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
pos_val = 0.0   # MARKET value of every position: pairs ~= $1, naked at its price (not $0)
try:
    pos = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://data-api.polymarket.com/positions?user=%s&sizeThreshold=0.3&limit=100" % proxy,
        headers={"User-Agent": "Mozilla/5.0"}), timeout=15))
    for p in pos:
        pos_val += float(p.get("size", 0) or 0) * float(p.get("curPrice") or 0)
    # print ONLY inside the try: if the positions fetch/parse failed we must NOT publish an
    # equity that omits every position (=$15-committed false dip) — an empty print makes the
    # bash `[ -z "$EQ" ]` guard SKIP the read, so a data-api outage can't false-stop.
    print("%.2f %.2f %.2f %.2f" % (pusd, usdce, pos_val, pusd + usdce + pos_val))
except Exception:
    pass
PYEOF
)
  if [ -z "$EQ" ]; then echo "$(date -u +%T) NO-EQUITY-READ"; sleep 10; continue; fi
  read -r PUSD USDCE POSVAL TOTAL <<< "$EQ"
  [ -f "$BASE_F" ] || echo "$TOTAL" > "$BASE_F"
  BASE=$(cat "$BASE_F")
  DD=$(python3 -c "print(round($TOTAL-$BASE,2))")
  S=$(curl -s -m4 http://127.0.0.1:8080/api/status)
  MODE=$(echo "$S" | python3 -c "import sys,json;print(json.load(sys.stdin).get('mode'))" 2>/dev/null)
  NAKED=$(echo "$S" | python3 -c "import sys,json;print(int(json.load(sys.stdin).get('naked_shares') or 0))" 2>/dev/null)
  MF_NOW=$(grep -c merge_failed "$LOG" 2>/dev/null || echo 0)
  MF=$((MF_NOW - MF_BASE))                      # merges failed SINCE we started
  # equity-dd debounce: count consecutive breaches, reset on any healthy read
  if python3 -c "exit(0 if float('$DD') < $LIMIT else 1)"; then
    DD_COUNT=$((DD_COUNT + 1))
  else
    DD_COUNT=0
  fi
  echo "$(date -u +%T) mode=$MODE equity=$TOTAL (pUSD=$PUSD usdce=$USDCE posval=\$$POSVAL) dd=$DD (x$DD_COUNT) naked=$NAKED merge_fails=$MF"
  BREACH=""
  [ "$DD_COUNT" -ge "$DD_NEEDED" ] && BREACH="equity dd<$LIMIT x$DD_COUNT"
  [ "${NAKED:-0}" -gt 8 ] 2>/dev/null && BREACH="naked>8"
  [ "${MF:-0}" -ge 2 ] 2>/dev/null && BREACH="merge_failed x$MF"
  if [ -n "$BREACH" ] && [ "$MODE" = "RUNNING" ]; then
    echo "$(date -u +%T) *** BREACH: $BREACH -> FORCE STOP ***"
    curl -s -m5 -X POST http://127.0.0.1:8080/api/force_stop
  fi
  sleep 10
done

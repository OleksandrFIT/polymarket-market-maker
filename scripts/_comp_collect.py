"""Durable collector of 0xb27b activity — beats the /activity 3500-record truncation by polling
the newest pages frequently and deduping, so per-window data is COMPLETE. Writes JSONL; append-safe
(resumes without dupes). Run: python3 scripts/_comp_collect.py <out.jsonl> [poll_sec] [minutes]"""
import sys
import os
import json
import time
import urllib.request

ADDR = "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82"
UA = {"User-Agent": "Mozilla/5.0"}
OUT = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/comp_today.jsonl"
POLL = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
RUN_MIN = float(sys.argv[3]) if len(sys.argv) > 3 else 180.0
PAGES = (0, 500, 1000, 1500, 2000)          # newest ~2500 records each cycle (~40 min of history)


def key(x):
    return (x.get("transactionHash", ""), x.get("type", ""), x.get("asset", ""),
            round(float(x.get("usdcSize", 0)), 4), round(float(x.get("size", 0)), 4))


def main():
    seen = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                seen.add(key(json.loads(line)))
            except (ValueError, TypeError):
                pass
    print("resume: %d existing events" % len(seen), flush=True)
    end = time.time() + RUN_MIN * 60
    f = open(OUT, "a")
    total = 0
    while time.time() < end:
        added = 0
        for off in PAGES:
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(
                    "https://data-api.polymarket.com/activity?user=%s&limit=500&offset=%d" % (ADDR, off),
                    headers=UA), timeout=20))
            except Exception:
                break
            if not r:
                break
            for x in r:
                k = key(x)
                if k in seen:
                    continue
                seen.add(k)
                f.write(json.dumps(x) + "\n")
                added += 1
                total += 1
            f.flush()
        print("+%d new (total %d, seen %d)" % (added, total, len(seen)), flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()

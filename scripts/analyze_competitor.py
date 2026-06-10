"""Measure Bonereaper's 5m up/down edge: does his pair-spread survive his naked legs?

Read-only. Paginates his BUY trades by TIMESTAMP cursor (the data-api offset paging
400s past ~3000), across ALL 5m up/down crypto windows (btc/eth/xrp/sol — identical
strategy, larger sample), resolves each window's winner via the CLOB market endpoint,
reconstructs per-window P&L (hedge vs naked) and prints the go/no-go report.

Run: .venv/bin/python scripts/analyze_competitor.py
"""

import json
import time
import urllib.request
from collections import defaultdict

from quoter.analysis.competitor import Trade, aggregate, reconstruct_window

COMP = "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
MARKET_PREFIXES = ("btc-updown-5m", "eth-updown-5m", "xrp-updown-5m", "sol-updown-5m")
TARGET_WINDOWS = 120
MAX_PAGES = 50            # timestamp-cursor pages of 500 trades each
PAGE = 500


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def fetch_trades():
    """Paginate his activity by timestamp cursor; collect 5m up/down BUY trades grouped
    by conditionId. Stops at TARGET_WINDOWS distinct windows or MAX_PAGES."""
    by_window = defaultdict(lambda: {"slug": "", "trades": []})
    end = None
    for page in range(MAX_PAGES):
        url = (f"https://data-api.polymarket.com/activity?user={COMP}"
               f"&limit={PAGE}&sortBy=TIMESTAMP&sortDirection=DESC")
        if end is not None:
            url += f"&end={end}"
        try:
            acts = _get(url)
        except Exception as e:
            print(f"  page {page} fetch error: {e}")
            break
        if not acts:
            break
        for a in acts:
            slug = a.get("slug", "")
            if (a.get("type") == "TRADE" and a.get("side") == "BUY"
                    and any(slug.startswith(p) for p in MARKET_PREFIXES)
                    and a.get("outcome") in ("Up", "Down")):
                w = by_window[a["conditionId"]]
                w["slug"] = slug
                w["trades"].append(Trade(a["outcome"], float(a["size"]), float(a["price"])))
        if len(by_window) >= TARGET_WINDOWS:
            break
        new_end = min(int(a["timestamp"]) for a in acts) - 1   # cursor: just before oldest
        if end is not None and new_end >= end:
            break   # no progress → stop
        end = new_end
        time.sleep(0.2)
    return by_window


def fetch_winner(condition_id):
    """Return 'Up'/'Down' for a resolved window, or None if not resolved/unknown."""
    try:
        cm = _get(f"https://clob.polymarket.com/markets/{condition_id}")
    except Exception:
        return None
    if not cm.get("closed"):
        return None
    for t in cm.get("tokens", []):
        if t.get("winner") is True or float(t.get("price", 0)) >= 0.99:
            return t.get("outcome")
    return None


def main():
    print(f"Pulling Bonereaper 5m up/down trades (target {TARGET_WINDOWS} windows)...")
    by_window = fetch_trades()
    print(f"  collected {len(by_window)} candidate windows; resolving winners...")

    results = []
    skipped = 0
    for cid, w in by_window.items():
        winner = fetch_winner(cid)
        if winner is None:
            skipped += 1
            continue
        results.append(reconstruct_window(w["slug"], w["trades"], winner))
        time.sleep(0.05)

    rep = aggregate(results)
    print("\n" + "=" * 60)
    print("COMPETITOR EDGE ANALYSIS — Bonereaper 5m up/down (btc/eth/xrp/sol)")
    print("=" * 60)
    print(f"Windows analyzed (resolved): {rep.n_windows}   (skipped unresolved: {skipped})")
    print(f"Hedged windows:  {rep.n_hedged}   avg pair ${rep.avg_pair_cost:.3f}")
    print(f"Naked windows:   {rep.n_naked}")
    print(f"Avg size/window: {rep.avg_size_per_window:.0f} shares   total spend ${rep.total_spend:,.0f}")
    print("-" * 60)
    print(f"  Pair P&L  (hedge edge):  ${rep.total_pair_pnl:+,.2f}")
    print(f"  Naked P&L (directional): ${rep.total_naked_pnl:+,.2f}")
    print(f"  NET TOTAL:               ${rep.total_net:+,.2f}")
    print(f"  Net / window:            ${rep.net_per_window:+.3f}")
    print(f"  Windows net-positive:    {rep.pct_windows_positive:.0f}%")
    print("-" * 60)
    print(f"  VERDICT: {rep.verdict}")
    print("=" * 60)


if __name__ == "__main__":
    main()

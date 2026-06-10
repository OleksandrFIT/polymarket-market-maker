"""Measure Bonereaper's BTC 5m edge: does his pair-spread survive his naked legs?

Read-only. Pulls his BUY trades (paginated), resolves each window's winner via the
CLOB market endpoint, reconstructs per-window P&L (hedge vs naked), and prints the
go/no-go report. Sample method B: ~TARGET_WINDOWS most-recent RESOLVED BTC 5m windows.

Run: .venv/bin/python scripts/analyze_competitor.py
"""

import json
import time
import urllib.request
from collections import defaultdict

from quoter.analysis.competitor import Trade, aggregate, reconstruct_window

COMP = "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
TARGET_WINDOWS = 120      # method B sample size
MAX_PAGES = 60            # pagination guard (60 * 500 = 30k trades)
PAGE = 500


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def fetch_btc_trades():
    """Paginate his activity; collect BTC 5m BUY trades grouped by (conditionId, slug).
    Stops once we have >= TARGET_WINDOWS distinct windows or hit MAX_PAGES."""
    by_window = defaultdict(lambda: {"slug": "", "trades": []})
    for page in range(MAX_PAGES):
        url = (f"https://data-api.polymarket.com/activity?user={COMP}"
               f"&limit={PAGE}&offset={page * PAGE}&sortBy=TIMESTAMP&sortDirection=DESC")
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
                    and slug.startswith("btc-updown-5m")
                    and a.get("outcome") in ("Up", "Down")):
                w = by_window[a["conditionId"]]
                w["slug"] = slug
                w["trades"].append(Trade(a["outcome"], float(a["size"]), float(a["price"])))
        if len(by_window) >= TARGET_WINDOWS:
            break
        time.sleep(0.2)   # gentle on the API
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
    print(f"Pulling Bonereaper BTC 5m trades (target {TARGET_WINDOWS} windows)...")
    by_window = fetch_btc_trades()
    print(f"  collected {len(by_window)} candidate windows; resolving winners...")

    results = []
    skipped = 0
    for cid, w in by_window.items():
        winner = fetch_winner(cid)
        if winner is None:
            skipped += 1
            continue
        results.append(reconstruct_window(w["slug"], w["trades"], winner))
        time.sleep(0.1)

    rep = aggregate(results)
    print("\n" + "=" * 60)
    print(f"COMPETITOR EDGE ANALYSIS — Bonereaper BTC 5m")
    print("=" * 60)
    print(f"Windows analyzed (resolved): {rep.n_windows}   (skipped unresolved: {skipped})")
    print(f"Hedged windows:  {rep.n_hedged}   avg pair ${rep.avg_pair_cost:.3f}")
    print(f"Naked windows:   {rep.n_naked}")
    print(f"Avg size/window: {rep.avg_size_per_window:.0f} shares   total spend ${rep.total_spend:,.0f}")
    print("-" * 60)
    print(f"  Pair P&L  (hedge edge): ${rep.total_pair_pnl:+,.2f}")
    print(f"  Naked P&L (directional): ${rep.total_naked_pnl:+,.2f}")
    print(f"  NET TOTAL:               ${rep.total_net:+,.2f}")
    print(f"  Net / window:            ${rep.net_per_window:+.3f}")
    print(f"  Windows net-positive:    {rep.pct_windows_positive:.0f}%")
    print("-" * 60)
    print(f"  VERDICT: {rep.verdict}")
    print("=" * 60)


if __name__ == "__main__":
    main()

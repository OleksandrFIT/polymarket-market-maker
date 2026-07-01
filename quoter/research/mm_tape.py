"""Tape loader for the MM simulator. Pure helpers (normalize_trades, ticks_from_tape)
are unit-tested; the network loaders are thin I/O reusing validated data-api/gamma
patterns. Cache raw JSON to /tmp/poly_mm_cache."""
from __future__ import annotations

import json
import os
import time
import urllib.request

CACHE = "/tmp/poly_mm_cache"
UA = {"User-Agent": "Mozilla/5.0"}


def normalize_trades(raw: list) -> list:
    out = []
    for t in raw:
        try:
            out.append({
                "ts": int(t["timestamp"]),
                "side": t["side"],
                "oi": int(t["outcomeIndex"]),
                "price": float(t["price"]),
                "size": float(t["size"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda t: t["ts"])
    return out


def ticks_from_tape(tape: list, open_ts: int, step_sec: int) -> list:
    if not tape:
        return [(open_ts, 0.5)]
    end = tape[-1]["ts"]
    ticks = []
    up_prices = [(t["ts"], t["price"]) for t in tape if t["oi"] == 0]
    ts = open_ts
    while ts <= end:
        mid = 0.5
        for uts, up in up_prices:
            if uts <= ts:
                mid = up
            else:
                break
        ticks.append((ts, mid))
        ts += step_sec
    return ticks


# ── pure subgraph-fill aggregation (unit-tested) ───────────────────────────
def decode_maker_buy(ev):
    """Return {"tid": str, "price": float, "size": float} for a BUY maker fill, or None
    for a SELL (makerAssetId != '0') or malformed event.

    A BUY fill has makerAssetId == "0" (USDC out): the bought token is takerAssetId,
    shares = takerAmountFilled/1e6, price = makerAmountFilled/takerAmountFilled."""
    try:
        if str(ev["makerAssetId"]) != "0":
            return None
        tid = str(ev["takerAssetId"])
        maker_amt = float(ev["makerAmountFilled"])
        taker_amt = float(ev["takerAmountFilled"])
        if taker_amt <= 0:
            return None
        return {"tid": tid, "price": maker_amt / taker_amt, "size": taker_amt / 1e6}
    except (KeyError, TypeError, ValueError):
        return None


def aggregate_fills(decoded):
    """decoded: list of {"slug","side","price","size"}. Return {slug: {"slug","size_up",
    "avg_up","size_dn","avg_dn"}} with cost-weighted avg prices per side."""
    acc = {}
    for d in decoded:
        slug = d["slug"]
        side = d["side"]
        price = float(d["price"])
        size = float(d["size"])
        a = acc.setdefault(slug, {"size_up": 0.0, "cost_up": 0.0,
                                  "size_dn": 0.0, "cost_dn": 0.0})
        if side == "Up":
            a["size_up"] += size
            a["cost_up"] += price * size
        else:
            a["size_dn"] += size
            a["cost_dn"] += price * size
    out = {}
    for slug, a in acc.items():
        out[slug] = {
            "slug": slug,
            "size_up": a["size_up"],
            "avg_up": a["cost_up"] / a["size_up"] if a["size_up"] else 0.0,
            "size_dn": a["size_dn"],
            "avg_dn": a["cost_dn"] / a["size_dn"] if a["size_dn"] else 0.0,
        }
    return out


# ── thin I/O (not unit-tested) ─────────────────────────────────────────────
def _get(url, tries=4):
    for k in range(tries):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:
            if k == tries - 1:
                return None
            time.sleep(0.6)


def _cached(key, fetch):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    val = fetch()
    if val is not None:
        json.dump(val, open(path, "w"))
    return val


def load_window(slug):
    """Return (tape, winner, open_ts) for a 5m window slug, or None."""
    g = _cached("m_" + slug, lambda: _get(
        "https://gamma-api.polymarket.com/markets?slug=%s&closed=true" % slug))
    if not (isinstance(g, list) and g):
        return None
    cond = g[0].get("conditionId")
    op = g[0].get("outcomePrices")
    if isinstance(op, str):
        op = json.loads(op)
    winner = "Up" if op and float(op[0]) >= 0.99 else ("Down" if op and float(op[1]) >= 0.99 else None)
    if winner is None or not cond:
        return None
    raw = _cached("t_" + slug, lambda: _fetch_trades(cond))
    tape = normalize_trades(raw or [])
    open_ts = int(slug.rsplit("-", 1)[1])
    return tape, winner, open_ts


def _fetch_trades(cond):
    out, off = [], 0
    while off < 3500:
        b = _get("https://data-api.polymarket.com/trades?market=%s&limit=500&offset=%d" % (cond, off))
        if not isinstance(b, list) or not b:
            break
        out += b
        if len(b) < 500:
            break
        off += 500
    return out


def competitor_targets(addr):
    """Real per-window end-state from open both-sided positions:
    [{slug, size_up, size_dn, avg_up, avg_dn}]."""
    r = _get("https://data-api.polymarket.com/positions?user=%s&sizeThreshold=1&limit=500" % addr)
    if not isinstance(r, list):
        return []
    byslug = {}
    for p in r:
        s = p.get("slug", "")
        if "-5m-" not in s:
            continue
        d = byslug.setdefault(s, {})
        oc = "Up" if p.get("outcome") == "Up" else "Down"
        d[oc] = (float(p.get("size", 0)), float(p.get("avgPrice", 0)))
    tgts = []
    for s, d in byslug.items():
        if "Up" in d and "Down" in d:      # both-sided => pre-merge gross visible
            tgts.append({"slug": s, "size_up": d["Up"][0], "avg_up": d["Up"][1],
                         "size_dn": d["Down"][0], "avg_dn": d["Down"][1]})
    return tgts

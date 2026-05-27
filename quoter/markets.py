"""Discover active Polymarket 5m/15m Up-or-Down crypto markets.

Polymarket auto-generates these markets every N minutes by URL convention:
  ``https://polymarket.com/event/{asset}-updown-{timeframe}-{open_ts}``

Gamma API does NOT list them (verified). We HTTP-GET the event URL,
parse ``clobTokenIds`` from the HTML, and assemble a ``Market`` record.

Returns ``Market`` records with ``negativeRisk=False`` only — neg-risk
markets have different settlement mechanics and are skipped.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

from quoter.config import Config
from quoter.ops.logger import get_logger

log = get_logger("markets")

_TIMEFRAME_SEC: dict[str, int] = {"5m": 300, "15m": 900}

_TOKEN_RE = re.compile(r'"clobTokenIds"\s*:\s*\["(\d+)"\s*,\s*"(\d+)"\]')
_TOKEN_RE_ALT = re.compile(r'"clobTokenIds":\s*"\[\\"(\d+)\\",\\"(\d+)\\"\]"')
_NEGRISK_RE = re.compile(r'"negativeRisk"\s*:\s*(true|false)')
_CONDITION_RE = re.compile(r'"conditionId"\s*:\s*"(0x[a-fA-F0-9]+)"')


@dataclass(frozen=True)
class Market:
    """Single active Up/Down market.

    ``yes_token``/``no_token`` are the ERC-1155 token IDs traded on CLOB.
    ``strike`` is BTC/ETH price at window open; populated lazily (may be 0
    until first book mid arrives — see ``strike.py``).
    """

    market_id: str  # conditionId, 0x-prefix
    asset: str  # "BTC" | "ETH"
    timeframe: str  # "5m" | "15m"
    yes_token: str
    no_token: str
    slug: str
    open_ts: int
    expire_ts: int
    strike: float = 0.0
    negative_risk: bool = False

    def time_remaining(self, now: float | None = None) -> float:
        return self.expire_ts - (now if now is not None else time.time())


async def discover_markets(cfg: Config, min_time_remaining_sec: int = 30) -> list[Market]:
    """Return all currently-active Up/Down markets for configured assets×timeframes.

    Filters out:
      * negativeRisk markets
      * markets with ``expires_in <= min_time_remaining_sec`` — too late to
        bother quoting in the dying seconds of a window.

    Each call constructs the current window timestamp from local clock;
    caller should re-discover periodically to pick up new windows.
    """
    now = time.time()
    out: list[Market] = []
    async with httpx.AsyncClient(
        http2=True,
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0 (poly-quoter)"},
        follow_redirects=True,
    ) as client:
        for asset in cfg.assets:
            for tf in cfg.timeframes:
                if tf not in _TIMEFRAME_SEC:
                    continue
                window_sec = _TIMEFRAME_SEC[tf]
                open_ts = int(now) // window_sec * window_sec
                slug = f"{asset.lower()}-updown-{tf}-{open_ts}"
                market = await _fetch_one(client, cfg, asset, tf, slug, open_ts, window_sec)
                if not market:
                    log.debug("market_not_found", asset=asset, tf=tf, slug=slug)
                    continue
                if market.time_remaining(now) <= min_time_remaining_sec:
                    log.debug(
                        "market_too_late",
                        asset=asset, tf=tf,
                        expires_in=int(market.time_remaining(now)),
                    )
                    continue
                out.append(market)
    log.info(
        "discover_markets",
        found=len(out), assets=list(cfg.assets), timeframes=list(cfg.timeframes),
    )
    return out


async def _fetch_one(
    client: httpx.AsyncClient,
    cfg: Config,
    asset: str,
    tf: str,
    slug: str,
    open_ts: int,
    window_sec: int,
) -> Market | None:
    url = f"{cfg.polymarket_web}/event/{slug}"
    try:
        r = await client.get(url)
    except httpx.HTTPError as e:
        log.warning("discover_http_error", slug=slug, error=str(e))
        return None
    if r.status_code != 200:
        return None
    html = r.text
    # Try both serialization styles
    m = _TOKEN_RE.search(html) or _TOKEN_RE_ALT.search(html)
    if not m:
        return None
    yes_token, no_token = m.group(1), m.group(2)

    cm = _CONDITION_RE.search(html)
    if not cm:
        log.warning("no_condition_id", slug=slug)
        return None
    condition_id = cm.group(1)

    nrm = _NEGRISK_RE.search(html)
    negative_risk = (nrm.group(1) == "true") if nrm else False
    if negative_risk:
        log.info("skipping_negative_risk", slug=slug)
        return None

    return Market(
        market_id=condition_id,
        asset=asset,
        timeframe=tf,
        yes_token=yes_token,
        no_token=no_token,
        slug=slug,
        open_ts=open_ts,
        expire_ts=open_ts + window_sec,
        strike=0.0,  # filled later from gamma metadata or book mid
        negative_risk=False,
    )

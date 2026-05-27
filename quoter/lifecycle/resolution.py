"""Poll Polymarket CLOB for official market resolution.

After a window closes, Polymarket resolves the market via its Chainlink/UMA
oracle. The CLOB ``GET /markets/{condition_id}`` endpoint reports ``closed``
and a per-token ``winner`` flag once settlement lands. We map the winning
``token_id`` back to YES/NO using the ``Market``'s known token ids — matching
by id (not the ``outcome`` string) so "Up"/"Down" labelling can't fool us.
"""

from __future__ import annotations

from typing import Any, Protocol

from quoter.markets import Market
from quoter.ops.logger import get_logger
from quoter.strategy.inventory import Side

log = get_logger("resolution")


class _ClientLike(Protocol):
    async def get(self, url: str) -> Any: ...


async def fetch_resolution(
    client: _ClientLike, clob_host: str, market: Market
) -> Side | None:
    """Return the winning side once ``market`` has resolved, else ``None``.

    ``None`` means "not resolved yet" — still open, closed without a winner
    flag, or (logged) a winning token that matches neither known token id.
    """
    try:
        r = await client.get(f"{clob_host}/markets/{market.market_id}")
    except Exception as e:  # network hiccup — treat as "not yet"
        log.debug("resolution_fetch_error", market=market.market_id[:12], error=str(e))
        return None
    if getattr(r, "status_code", None) != 200:
        return None
    data = r.json()
    if not data.get("closed"):
        return None
    winner_token: str | None = None
    for t in data.get("tokens", []):
        if t.get("winner"):
            winner_token = str(t.get("token_id"))
            break
    if winner_token is None:
        return None  # closed but oracle hasn't reported a winner yet
    if winner_token == market.yes_token:
        return "YES"
    if winner_token == market.no_token:
        return "NO"
    log.warning(
        "resolution_token_mismatch",
        market=market.market_id[:12], winner_token=winner_token,
    )
    return None

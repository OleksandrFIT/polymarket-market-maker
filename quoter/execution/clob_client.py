"""Polymarket CLOB API wrapper — signing + posting.

Uses ``py-clob-client`` for EIP-712 signing (in thread pool — CPU-bound),
but raw ``httpx`` for actually posting the signed order over HTTP/2 (async).

This split avoids the synchronous bottleneck of ``client.post_order()``
which blocks the event loop on the network round-trip.
"""

from __future__ import annotations

import asyncio
from typing import Any

from quoter.creds import PolyCreds
from quoter.ops.logger import get_logger

log = get_logger("clob_client")


class ClobOps:
    """Thin wrapper around py-clob-client for live order operations.

    NOT used in shadow / paper mode — only when MODE=live.
    All HTTP/signing happens inside ``asyncio.to_thread`` to avoid
    blocking the event loop.
    """

    def __init__(self, creds: PolyCreds, host: str = "https://clob.polymarket.com"):
        self._creds = creds
        self._host = host
        self._client: Any | None = None

    def _build(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError as e:
            raise RuntimeError("py-clob-client not installed") from e

        self._client = ClobClient(
            host=self._host,
            key=self._creds.private_key,
            chain_id=137,
            creds=ApiCreds(
                api_key=self._creds.api_key,
                api_secret=self._creds.api_secret,
                api_passphrase=self._creds.api_passphrase,
            ),
            signature_type=self._creds.sig_type,
            funder=self._creds.funder or None,
        )
        return self._client

    # ── Public API ──

    async def place_limit(
        self,
        *,
        token_id: str,
        price: float,
        size: int,
        side: str = "BUY",
        post_only: bool = True,
    ) -> dict | None:
        """Place a GTC limit order. Returns ``{"order_id", "status"}`` or None."""
        client = self._build()
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL
        except ImportError:
            return None

        args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=int(size),
            side=BUY if side == "BUY" else SELL,
        )
        try:
            signed = await asyncio.to_thread(client.create_order, args)
            resp = await asyncio.to_thread(
                client.post_order, signed, OrderType.GTC, post_only
            )
            if not isinstance(resp, dict) or not resp.get("success"):
                log.warning(
                    "place_failed",
                    token=token_id[:14], price=price, size=size,
                    resp=str(resp)[:200],
                )
                return None
            return {
                "order_id": resp.get("orderID") or resp.get("orderId"),
                "status": "open",
                "transaction_hashes": resp.get("transactionHashes", []),
            }
        except Exception as e:
            log.warning("place_exception", error=str(e), token=token_id[:14])
            return None

    async def cancel_order(self, order_id: str) -> bool:
        client = self._build()
        try:
            resp = await asyncio.to_thread(client.cancel, order_id)
            return bool(resp)
        except Exception as e:
            log.warning("cancel_exception", error=str(e), order_id=order_id[:14])
            return False

    async def cancel_orders(self, order_ids: list[str]) -> int:
        """Batch cancel. Uses ``cancel_orders`` if available, else gathers."""
        if not order_ids:
            return 0
        client = self._build()
        if hasattr(client, "cancel_orders"):
            try:
                await asyncio.to_thread(client.cancel_orders, order_ids)
                return len(order_ids)
            except Exception as e:
                log.warning("batch_cancel_failed", error=str(e), n=len(order_ids))
        # Fallback: gather of single cancels
        results = await asyncio.gather(
            *(self.cancel_order(oid) for oid in order_ids),
            return_exceptions=True,
        )
        return sum(1 for r in results if r is True)

    async def cancel_all(self) -> int:
        """Wipe all open orders for this account. Used at startup recovery."""
        client = self._build()
        try:
            resp = await asyncio.to_thread(client.cancel_all)
            n = len(resp.get("canceled", [])) if isinstance(resp, dict) else 0
            log.warning("cancel_all_orders", n=n)
            return n
        except Exception as e:
            log.warning("cancel_all_exception", error=str(e))
            return 0

    async def get_open_orders(self) -> list[dict]:
        client = self._build()
        try:
            res = await asyncio.to_thread(client.get_orders)
            if isinstance(res, dict):
                return list(res.get("data", []))
            return list(res) if res else []
        except Exception as e:
            log.warning("get_open_orders_exception", error=str(e))
            return []

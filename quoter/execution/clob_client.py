"""Polymarket CLOB **V2** API wrapper — signing + posting.

Migrated to ``py-clob-client-v2`` after the CLOB V2 cutover (2026-04-28), which
retired the V1 SDKs (orders signed with the old EIP-712 v1 struct are rejected
with "invalid order version"). V2 removes ``feeRateBps`` from the signed order
(maker/limit orders are fee-free; fees are set by the operator at match time).

All HTTP/signing runs inside ``asyncio.to_thread`` to avoid blocking the loop.
The public method surface (``place_limit``/``cancel_orders``/``cancel_all``/
``get_open_orders``) is unchanged so ``LiveExecutor`` needs no edits.
"""

from __future__ import annotations

import asyncio
from typing import Any

from quoter.creds import PolyCreds
from quoter.ops.logger import get_logger

log = get_logger("clob_client")


class ClobOps:
    """Thin async wrapper around py-clob-client-v2 for live order operations.

    NOT used in shadow / paper mode — only when MODE=live.
    """

    def __init__(self, creds: PolyCreds, host: str = "https://clob.polymarket.com"):
        self._creds = creds
        self._host = host
        self._client: Any | None = None

    def _build(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from py_clob_client_v2 import ClobClient, ApiCreds
        except ImportError as e:
            raise RuntimeError("py-clob-client-v2 not installed") from e

        self._client = ClobClient(
            self._host,
            137,
            key=self._creds.private_key,
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
        order_type: str = "GTC",
    ) -> dict | None:
        """Place a GTC limit order (V2). Returns ``{"order_id", "status"}`` or None."""
        client = self._build()
        try:
            from py_clob_client_v2 import OrderArgsV2, OrderType, Side
        except ImportError:
            return None

        args = OrderArgsV2(
            token_id=token_id,
            price=float(price),
            size=int(size),
            side=Side.BUY if side == "BUY" else Side.SELL,
        )
        ot = {"FOK": OrderType.FOK, "FAK": OrderType.FAK}.get(order_type, OrderType.GTC)
        try:
            signed = await asyncio.to_thread(client.create_order, args)
            resp = await asyncio.to_thread(
                client.post_order, signed, ot, post_only
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
                "status": resp.get("status", "live"),
            }
        except Exception as e:
            # Log the FULL order params, not just the error. Live run #1 (2026-07-16) hit
            # 'invalid maker amount' x26 on the sell-loser path and the root cause was NOT
            # diagnosable afterwards, because this branch recorded only error+token — no price,
            # size, side or order_type. The sibling `place_failed` already logs price/size; this
            # path must too, or a rejected order is an unexplainable event.
            log.warning("place_exception", error=str(e), token=token_id[:14],
                        price=price, size=size, side=side,
                        order_type=order_type, post_only=post_only)
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order by hash."""
        n = await self.cancel_orders([order_id])
        return n > 0

    async def cancel_orders(self, order_ids: list[str]) -> int:
        """Batch cancel by order hashes (V2 ``cancel_orders`` takes a list)."""
        if not order_ids:
            return 0
        client = self._build()
        try:
            resp = await asyncio.to_thread(client.cancel_orders, order_ids)
            if isinstance(resp, dict):
                return len(resp.get("canceled", []) or [])
            return len(order_ids)
        except Exception as e:
            log.warning("batch_cancel_failed", error=str(e), n=len(order_ids))
            return 0

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
            res = await asyncio.to_thread(client.get_open_orders)
            if isinstance(res, dict):
                return list(res.get("data", []))
            return list(res) if res else []
        except Exception as e:
            log.warning("get_open_orders_exception", error=str(e))
            return []

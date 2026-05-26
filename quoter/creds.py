"""Polymarket API credentials loaded from .env.

Loaded once at startup. Required for any non-public CLOB or User WS operation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PolyCreds:
    """Polymarket CLOB credentials.

    Source: .env (POLY_* keys generated via Polymarket onboarding).

    Attributes:
        private_key: 0x-prefixed hex EOA private key (signs EIP-712 orders).
        api_key:     UUID-format identifier for CLOB API.
        api_secret:  Base64-encoded HMAC key for request signing.
        api_passphrase: Plain string passphrase.
        funder:      Proxy wallet address that holds USDC (used when sig_type=1/2).
        sig_type:    0 = EOA, 1 = Polymarket proxy, 2 = Gnosis safe.
    """

    private_key: str
    api_key: str
    api_secret: str
    api_passphrase: str
    funder: str
    sig_type: int

    @classmethod
    def from_env(cls) -> PolyCreds:
        missing = [
            k for k in ("POLY_PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE")
            if not os.environ.get(k, "").strip()
        ]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
        return cls(
            private_key=os.environ["POLY_PRIVATE_KEY"].strip(),
            api_key=os.environ["POLY_API_KEY"].strip(),
            api_secret=os.environ["POLY_API_SECRET"].strip(),
            api_passphrase=os.environ["POLY_API_PASSPHRASE"].strip(),
            funder=os.environ.get("POLY_FUNDER_ADDRESS", "").strip(),
            sig_type=int(os.environ.get("POLY_SIGNATURE_TYPE", "1")),
        )

    def auth_dict(self) -> dict[str, str]:
        """Auth payload for User WS subscription."""
        return {
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "passphrase": self.api_passphrase,
        }

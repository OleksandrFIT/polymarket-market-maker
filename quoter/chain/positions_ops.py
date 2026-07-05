"""On-chain position ops for the proxy wallet: merge matched pairs -> collateral,
redeem resolved, wrap stranded USDC.e -> pUSD (trading balance).

Chosen path documented in docs/superpowers/specs/2026-07-02-positions-ops-spike-notes.md.

Two execution paths, selected automatically:

* Path A (preferred, gasless): the official Polymarket relayer
  (``py-builder-relayer-client``) executes the call AS the proxy wallet, gas paid
  by Polymarket. Two auth schemes, resolved in order:
    1. NEW key auth — env ``POLY_RELAYER_API_KEY`` + ``POLY_RELAYER_API_KEY_ADDRESS``
       (Polymarket web UI: Settings > API Keys), sent as HTTP headers
       ``RELAYER_API_KEY`` / ``RELAYER_API_KEY_ADDRESS``.
    2. OLD HMAC builder auth — env ``POLY_BUILDER_API_KEY`` / ``POLY_BUILDER_SECRET``
       / ``POLY_BUILDER_PASSPHRASE`` (signed ``POLY_BUILDER_*`` headers).
* Path B (fallback, direct): the owner EOA sends the tx itself and pays POL gas
  (~$0.01/tx; keep ~1-2 POL on the EOA). For ``POLY_SIGNATURE_TYPE=1`` the EOA calls
  ``ProxyWalletFactory.proxy(calls)`` which routes through the EOA's proxy wallet;
  for sig type 0 the EOA calls the CTF directly. Sig type 2 (Gnosis Safe) is only
  supported via Path A.

Env consumed (names only — values are NEVER logged by this module):
    POLY_PRIVATE_KEY        owner EOA key (signs relayer request / direct tx)
    POLY_FUNDER_ADDRESS     proxy wallet address holding the positions
    POLY_SIGNATURE_TYPE     0 = EOA, 1 = Polymarket proxy, 2 = Gnosis Safe (default 1)
    POLY_RELAYER_API_KEY          \\ Path A NEW relayer key auth (optional; takes
    POLY_RELAYER_API_KEY_ADDRESS  /  precedence over the HMAC triple below)
    POLY_BUILDER_API_KEY    \\
    POLY_BUILDER_SECRET      } Path A relayer HMAC creds (optional; enables gasless)
    POLY_BUILDER_PASSPHRASE /
    POLY_RELAYER_URL        relayer host (default https://relayer-v2.polymarket.com)
    POLY_RPC_URL            Polygon JSON-RPC (default https://polygon-rpc.com)

Self-check (read-only, NO transactions):
    python -m quoter.chain.positions_ops --check [condition_id]

Contract addresses (verified on polygonscan 2026-07-02 + on-chain probes 2026-07-05,
see spike notes):
    ConditionalTokens  0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
    pUSD (v2 collat.)  0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB
    USDC.e (legacy)    0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
    ProxyWalletFactory 0xaB45c5A4B0c941a2F231C04C3f49182e1A254052
    pUSD wrapper       0x93070a847efEf7F70739046A929D47a521F5B8ee  (permissionless
                       ``wrap(address token, address to, uint256 amount)``; holds the
                       wrapper role on pUSD — pUSD.wrap itself is role-gated)

Collateral is auto-detected per condition (USDC.e first, then pUSD) by reading the
proxy's ERC-1155 pair balances — position ids depend on the collateral token, so
passing the wrong one would revert. Verified 2026-07-02: live 5m market CLOB token
ids equal the USDC.e-derived CTF position ids.

NEVER places orders. ``merge_pairs`` never burns more than the passed qty and
refuses when the wallet holds less. Merge/redeem make no token approvals (they
burn the caller's own ERC-1155 balance). ``wrap_usdce_to_pusd`` approves the
pUSD wrapper for EXACTLY the wrapped amount in the same batched proxy tx and
refuses when the proxy's USDC.e balance is below the requested amount.

Import-safe without web3: web3 and the relayer client are imported lazily inside
the functions that need them. Read-only chain queries use eth_abi + httpx only
(both already project deps).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from eth_abi.abi import decode as abi_decode
from eth_abi.abi import encode as abi_encode
from eth_utils.address import to_checksum_address
from eth_utils.crypto import keccak

log = structlog.get_logger(__name__)

# ── Verified Polygon mainnet addresses (see module docstring / spike notes) ──
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
PUSD_ADDRESS = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
USDCE_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PROXY_WALLET_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
# Permissionless USDC.e->pUSD wrapper (holds wrapper role 2 on pUSD; verified
# on-chain 2026-07-05: eth_call wrap(USDC.e, to, 0) from an arbitrary address
# succeeds, while pUSD.wrap directly reverts Unauthorized (0x82b42900) for
# non-wrappers). Live sample: proxy wallets call wrap(USDC.e, self, amount) —
# tx 0xacd202c41698720d3e489994eb89528d5b4c809fbf530c3c3eeef072c111e3ec.
PUSD_WRAPPER_ADDRESS = "0x93070a847efEf7F70739046A929D47a521F5B8ee"

DEFAULT_RELAYER_URL = "https://relayer-v2.polymarket.com"
# polygon-rpc.com now 401s without an API key; drpc is what Polymarket's own
# relayer client defaults to.
DEFAULT_RPC_URL = "https://polygon.drpc.org"
CHAIN_ID = 137
COLLATERAL_DECIMALS = 6
_PARENT_COLLECTION = b"\x00" * 32  # top-level positions only
_PARTITION = [1, 2]  # binary Up/Down index sets
_RPC_TIMEOUT_S = 15.0
_RECEIPT_TIMEOUT_S = 120.0

# Candidate collateral tokens, most likely first. Verified 2026-07-02 via eth_call:
# CLOB token ids of a live 5m market == CTF position ids derived with USDC.e
# (pUSD is an exchange-level wrapper; CTF positions stay USDC.e-collateralized).
_COLLATERAL_CANDIDATES = (USDCE_ADDRESS, PUSD_ADDRESS)


# ── Env helpers (values never logged) ──


def _rpc_url() -> str:
    return os.environ.get("POLY_RPC_URL", "").strip() or DEFAULT_RPC_URL


def _relayer_base_url() -> str:
    return os.environ.get("POLY_RELAYER_URL", "").strip() or DEFAULT_RELAYER_URL


def _proxy_address() -> str | None:
    addr = os.environ.get("POLY_FUNDER_ADDRESS", "").strip()
    return addr or None


def _sig_type() -> int:
    return int(os.environ.get("POLY_SIGNATURE_TYPE", "1"))


def _relayer_creds_present() -> bool:
    return all(
        os.environ.get(k, "").strip()
        for k in ("POLY_BUILDER_API_KEY", "POLY_BUILDER_SECRET", "POLY_BUILDER_PASSPHRASE")
    )


def _relayer_key_headers() -> dict[str, str] | None:
    """HTTP headers for the NEW relayer key-auth scheme, or None when not configured.

    Values are read from ``POLY_RELAYER_API_KEY`` / ``POLY_RELAYER_API_KEY_ADDRESS``
    and attached to relayer requests as headers only — never logged or printed.
    """
    key = os.environ.get("POLY_RELAYER_API_KEY", "").strip()
    address = os.environ.get("POLY_RELAYER_API_KEY_ADDRESS", "").strip()
    if key and address:
        return {"RELAYER_API_KEY": key, "RELAYER_API_KEY_ADDRESS": address}
    return None


def _relayer_key_auth_present() -> bool:
    return _relayer_key_headers() is not None


# ── ABI encoding (Gnosis ConditionalTokens) ──


def _selector(signature: str) -> bytes:
    return keccak(signature.encode())[:4]


def _condition_bytes(condition_id: str) -> bytes:
    cid = condition_id.lower().removeprefix("0x")
    raw = bytes.fromhex(cid)
    if len(raw) != 32:
        raise ValueError(f"condition_id must be 32 bytes, got {len(raw)}")
    return raw


def encode_merge(collateral: str, condition_id: str, qty_units: int) -> bytes:
    """calldata for CTF.mergePositions(collateral, 0x00, conditionId, [1,2], qty)."""
    return _selector("mergePositions(address,bytes32,bytes32,uint256[],uint256)") + abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]", "uint256"],
        [
            to_checksum_address(collateral),
            _PARENT_COLLECTION,
            _condition_bytes(condition_id),
            _PARTITION,
            qty_units,
        ],
    )


def encode_redeem(collateral: str, condition_id: str) -> bytes:
    """calldata for CTF.redeemPositions(collateral, 0x00, conditionId, [1,2])."""
    return _selector("redeemPositions(address,bytes32,bytes32,uint256[])") + abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            to_checksum_address(collateral),
            _PARENT_COLLECTION,
            _condition_bytes(condition_id),
            _PARTITION,
        ],
    )


def encode_erc20_approve(spender: str, amount_units: int) -> bytes:
    """calldata for ERC20.approve(spender, amount)."""
    return _selector("approve(address,uint256)") + abi_encode(
        ["address", "uint256"], [to_checksum_address(spender), amount_units]
    )


def encode_wrap(token: str, to: str, amount_units: int) -> bytes:
    """calldata for PUSD_WRAPPER.wrap(token, to, amount) — selector 0x62355638.

    The wrapper transferFrom's ``amount`` of ``token`` (USDC.e) from the caller
    into the pUSD contract, which mints pUSD 1:1 to ``to`` and forwards the
    USDC.e to the Polymarket vault (verified live 2026-07-05, see spike notes).
    """
    return _selector("wrap(address,address,uint256)") + abi_encode(
        ["address", "address", "uint256"],
        [to_checksum_address(token), to_checksum_address(to), amount_units],
    )


def _encode_factory_proxy_call(calls: list[tuple[str, bytes]]) -> bytes:
    """calldata for ProxyWalletFactory.proxy([(Call=1, to, 0, data), ...]) — Path B, sig type 1."""
    return _selector("proxy((uint8,address,uint256,bytes)[])") + abi_encode(
        ["(uint8,address,uint256,bytes)[]"],
        [[(1, to_checksum_address(to), 0, data) for to, data in calls]],
    )


# ── Read-only chain queries (httpx JSON-RPC; no web3 needed) ──


def _eth_call(to: str, data: bytes) -> bytes:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to_checksum_address(to), "data": "0x" + data.hex()}, "latest"],
    }
    resp = httpx.post(_rpc_url(), json=payload, timeout=_RPC_TIMEOUT_S)
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"eth_call error: {body['error']}")
    return bytes.fromhex(body["result"].removeprefix("0x"))


def _position_id(collateral: str, condition_id: str, index_set: int) -> int:
    """CTF.getCollectionId + CTF.getPositionId (view calls; collection id needs
    alt-bn128 math so it cannot be computed locally with a plain keccak)."""
    collection: bytes = _eth_call(
        CTF_ADDRESS,
        _selector("getCollectionId(bytes32,bytes32,uint256)")
        + abi_encode(
            ["bytes32", "bytes32", "uint256"],
            [_PARENT_COLLECTION, _condition_bytes(condition_id), index_set],
        ),
    )
    position = _eth_call(
        CTF_ADDRESS,
        _selector("getPositionId(address,bytes32)")
        + abi_encode(["address", "bytes32"], [to_checksum_address(collateral), collection]),
    )
    return int(abi_decode(["uint256"], position)[0])


def _balance_of(owner: str, position_id: int) -> int:
    raw = _eth_call(
        CTF_ADDRESS,
        _selector("balanceOf(address,uint256)")
        + abi_encode(["address", "uint256"], [to_checksum_address(owner), position_id]),
    )
    return int(abi_decode(["uint256"], raw)[0])


def _pair_balances(owner: str, collateral: str, condition_id: str) -> tuple[int, int]:
    return (
        _balance_of(owner, _position_id(collateral, condition_id, 1)),
        _balance_of(owner, _position_id(collateral, condition_id, 2)),
    )


def _erc20_balance_units(token: str, owner: str) -> int:
    raw = _eth_call(
        token, _selector("balanceOf(address)") + abi_encode(["address"], [to_checksum_address(owner)])
    )
    return int(abi_decode(["uint256"], raw)[0])


def usdce_balance() -> float | None:
    """Proxy wallet's on-chain USDC.e balance in dollars (read-only eth_call).

    Returns None when POLY_FUNDER_ADDRESS is unset or the RPC read fails —
    callers treat None as \"unknown, do nothing\". Never raises.
    """
    try:
        proxy = _proxy_address()
        if proxy is None:
            return None
        # float(): mypy types `int ** <int var>` as Any, tainting the division
        return float(_erc20_balance_units(USDCE_ADDRESS, proxy) / 10**COLLATERAL_DECIMALS)
    except Exception:
        log.exception("usdce_balance_error")
        return None


def _payout_denominator(condition_id: str) -> int:
    raw = _eth_call(
        CTF_ADDRESS,
        _selector("payoutDenominator(bytes32)")
        + abi_encode(["bytes32"], [_condition_bytes(condition_id)]),
    )
    return int(abi_decode(["uint256"], raw)[0])


def _detect_collateral(owner: str, condition_id: str, min_pair_units: int) -> str | None:
    """Pick the collateral whose position ids the proxy actually holds.

    ``min_pair_units > 0``: both legs must hold at least that many units (merge).
    ``min_pair_units == 0``: any leg with a positive balance qualifies (redeem).
    """
    for collateral in _COLLATERAL_CANDIDATES:
        up, down = _pair_balances(owner, collateral, condition_id)
        if min_pair_units > 0:
            if min(up, down) >= min_pair_units:
                return collateral
        elif up > 0 or down > 0:
            return collateral
    return None


# ── Execution: Path A (gasless relayer) ──


def _build_relay_client(tx_type: object) -> Any:
    """Construct a RelayClient using the strongest configured auth scheme.

    1. NEW key auth (``POLY_RELAYER_API_KEY`` + ``POLY_RELAYER_API_KEY_ADDRESS``):
       py-builder-relayer-client 0.0.2 (latest on PyPI, checked 2026-07-02) is
       HMAC-only; its auth headers are generated in exactly ONE place —
       ``RelayClient._post_request`` -> ``_generate_builder_headers`` (all GET
       endpoints are sent unauthenticated). Least invasive override: a subclass
       that replaces ``_post_request`` to attach the key-auth headers and no-ops
       ``assert_builder_creds_needed`` (the guard in ``execute()``); payload
       construction/signing (nonce, relay-payload, proxy encoding, EOA signature)
       is inherited unchanged.
    2. OLD HMAC builder creds (``POLY_BUILDER_*``): stock client behavior.

    Values of the env vars are passed to the client / headers only — never logged.
    """
    from py_builder_relayer_client.client import (  # type: ignore[import-untyped]
        RelayClient,  # lazy: optional dep
    )
    from py_builder_relayer_client.http_helpers.helpers import (  # type: ignore[import-untyped]
        post as relayer_post,
    )

    common: dict[str, Any] = {
        "relayer_url": _relayer_base_url(),
        "chain_id": CHAIN_ID,
        "private_key": os.environ["POLY_PRIVATE_KEY"].strip(),
        "relay_tx_type": tx_type,
        "rpc_url": _rpc_url(),
    }

    key_headers = _relayer_key_headers()
    if key_headers is not None:

        class _KeyAuthRelayClient(RelayClient):  # type: ignore[misc]
            """RelayClient authenticated with RELAYER_API_KEY headers (new scheme)."""

            def assert_builder_creds_needed(self) -> None:
                return None  # key-auth headers replace HMAC builder creds

            def _post_request(
                self, method: str, request_path: str, body: dict[str, Any] | None = None
            ) -> Any:
                return relayer_post(
                    f"{self.relayer_url}{request_path}", headers=key_headers, data=body
                )

        log.info("relayer_auth_mode", mode="key")
        return _KeyAuthRelayClient(**common)

    from py_builder_signing_sdk.config import BuilderConfig  # type: ignore[import-untyped]
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds  # type: ignore[import-untyped]

    log.info("relayer_auth_mode", mode="hmac")
    return RelayClient(
        builder_config=BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=os.environ["POLY_BUILDER_API_KEY"].strip(),
                secret=os.environ["POLY_BUILDER_SECRET"].strip(),
                passphrase=os.environ["POLY_BUILDER_PASSPHRASE"].strip(),
            )
        ),
        **common,
    )


def _execute_via_relayer(calls: list[tuple[str, bytes]]) -> bool:
    """Execute ``calls`` (a batch of ``(to, calldata)``) AS the proxy wallet via
    the Polymarket relayer (gasless), in ONE proxy transaction. Requires
    POLY_PRIVATE_KEY + one relayer auth scheme (key auth preferred, HMAC
    fallback — see ``_build_relay_client``)."""
    from py_builder_relayer_client.models import (  # type: ignore[import-untyped]
        RelayerTxType,  # lazy: optional dep
        Transaction,
    )

    sig_type = _sig_type()
    if sig_type == 1:
        tx_type = RelayerTxType.PROXY
    elif sig_type == 2:
        tx_type = RelayerTxType.SAFE
    else:
        log.warning("relayer_unsupported_sig_type", sig_type=sig_type)
        return False

    client = _build_relay_client(tx_type)
    resp = client.execute(
        [
            Transaction(to=to_checksum_address(to), data="0x" + data.hex(), value="0")
            for to, data in calls
        ],
        metadata="poly-quoter positions_ops",
    )
    mined = resp.wait()  # polls until MINED/CONFIRMED, None on FAILED/timeout
    ok = mined is not None
    log.info(
        "relayer_tx_done" if ok else "relayer_tx_failed",
        transaction_id=resp.transaction_id,
        tx_hash=resp.transaction_hash,
    )
    return ok


# ── Execution: Path B (direct on-chain, EOA pays POL gas) ──


def _execute_direct(calls: list[tuple[str, bytes]]) -> bool:
    """EOA sends the tx itself. sig type 1 -> ONE factory.proxy(calls) batch tx;
    0 -> the EOA is the wallet, so each call is its own sequential tx."""
    from web3 import Web3  # lazy: module stays importable without web3

    sig_type = _sig_type()
    if sig_type == 1:
        txs = [(PROXY_WALLET_FACTORY, _encode_factory_proxy_call(calls))]
    elif sig_type == 0:
        txs = calls
    else:
        log.warning("direct_path_unsupported_sig_type", sig_type=sig_type, hint="use relayer")
        return False

    w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": _RPC_TIMEOUT_S}))
    acct = w3.eth.account.from_key(os.environ["POLY_PRIVATE_KEY"].strip())
    for to, data in txs:
        tx: dict[str, object] = {  # loosely typed; web3 TxParams is a TypedDict
            "chainId": CHAIN_ID,
            "from": acct.address,
            "to": to_checksum_address(to),
            "value": 0,
            "data": data,
            "nonce": w3.eth.get_transaction_count(acct.address),
        }
        tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)  # type: ignore[arg-type]
        base_fee = w3.eth.get_block("latest").get("baseFeePerGas", 0)
        tip = max(w3.eth.max_priority_fee, w3.to_wei(30, "gwei"))  # polygon floor ~25-30 gwei
        tx["maxPriorityFeePerGas"] = tip
        tx["maxFeePerGas"] = base_fee * 2 + tip
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=_RECEIPT_TIMEOUT_S)
        ok = receipt["status"] == 1
        log.info("direct_tx_done" if ok else "direct_tx_reverted", tx_hash=tx_hash.hex())
        if not ok:
            return False
    return True


def _execute(calls: list[tuple[str, bytes]]) -> bool:
    """Route: relayer key-auth > relayer HMAC > direct on-chain (web3).

    ``calls`` is a list of ``(to, calldata)`` executed AS the proxy wallet — one
    batched proxy tx on the relayer / factory paths. Path A (gasless relayer) is
    taken when EITHER auth scheme is configured; inside it, key auth wins over
    HMAC (see ``_build_relay_client``).
    """
    if not os.environ.get("POLY_PRIVATE_KEY", "").strip():
        log.error("positions_ops_missing_private_key_env")
        return False
    if _relayer_key_auth_present() or _relayer_creds_present():
        return _execute_via_relayer(calls)
    return _execute_direct(calls)


# ── Public API ──


def merge_pairs(condition_id: str, qty: float) -> bool:
    """Burn ``qty`` Up+Down pairs of ``condition_id`` -> receive ``qty`` collateral.

    Refuses (returns False) when the proxy holds fewer than ``qty`` of either leg;
    never burns more than ``qty``. Blocking (network I/O) — call off the hot path.
    """
    try:
        units = int(round(qty * 10**COLLATERAL_DECIMALS))
        if units <= 0:
            log.warning("merge_skip_nonpositive_qty", qty=qty)
            return False
        proxy = _proxy_address()
        if proxy is None:
            log.error("merge_missing_funder_env")
            return False
        collateral = _detect_collateral(proxy, condition_id, units)
        if collateral is None:
            log.warning("merge_insufficient_pair", condition_id=condition_id, qty=qty)
            return False
        ok = _execute([(CTF_ADDRESS, encode_merge(collateral, condition_id, units))])
        log.info("merge_pairs_result", condition_id=condition_id, qty=qty, ok=ok)
        return ok
    except Exception:
        log.exception("merge_pairs_error", condition_id=condition_id, qty=qty)
        return False


def redeem(condition_id: str) -> bool:
    """Redeem the proxy's winning positions of a RESOLVED ``condition_id`` for collateral.

    Returns False when the condition is unresolved or the proxy holds no position.
    Blocking (network I/O) — call off the hot path.
    """
    try:
        proxy = _proxy_address()
        if proxy is None:
            log.error("redeem_missing_funder_env")
            return False
        if _payout_denominator(condition_id) == 0:
            log.info("redeem_skip_unresolved", condition_id=condition_id)
            return False
        collateral = _detect_collateral(proxy, condition_id, 0)
        if collateral is None:
            log.info("redeem_skip_no_position", condition_id=condition_id)
            return False
        ok = _execute([(CTF_ADDRESS, encode_redeem(collateral, condition_id))])
        log.info("redeem_result", condition_id=condition_id, ok=ok)
        return ok
    except Exception:
        log.exception("redeem_error", condition_id=condition_id)
        return False


def wrap_usdce_to_pusd(amount: float) -> bool:
    """Convert ``amount`` USDC.e held by the PROXY wallet into pUSD 1:1 (the CLOB
    trading balance) — merge/redeem return USDC.e, but orders buy with pUSD.

    Mechanism (verified on-chain 2026-07-05, see spike notes): ``pUSD.wrap`` is
    role-gated to registered wrapper contracts, so the public path is the
    permissionless wrapper ``PUSD_WRAPPER_ADDRESS`` —
    ``wrap(USDC.e, recipient, amount)`` transferFrom's the caller's USDC.e into
    the pUSD contract, which mints pUSD 1:1 to ``recipient`` and forwards the
    USDC.e to the Polymarket vault. Executed AS the proxy via the same relayer
    transport as merge/redeem, as ONE batched proxy tx:
    ``[USDC.e.approve(wrapper, amount), wrapper.wrap(USDC.e, proxy, amount)]``
    (exact-amount approval — no standing allowance is left behind).

    Refuses (returns False) when the proxy's on-chain USDC.e balance is below
    ``amount``. Blocking (network I/O) — call off the hot path.
    """
    try:
        units = int(round(amount * 10**COLLATERAL_DECIMALS))
        if units <= 0:
            log.warning("wrap_skip_nonpositive_amount", amount=amount)
            return False
        proxy = _proxy_address()
        if proxy is None:
            log.error("wrap_missing_funder_env")
            return False
        held = _erc20_balance_units(USDCE_ADDRESS, proxy)
        if held < units:
            log.warning("wrap_insufficient_usdce", amount=amount,
                        held=held / 10**COLLATERAL_DECIMALS)
            return False
        ok = _execute(
            [
                (USDCE_ADDRESS, encode_erc20_approve(PUSD_WRAPPER_ADDRESS, units)),
                (PUSD_WRAPPER_ADDRESS, encode_wrap(USDCE_ADDRESS, proxy, units)),
            ]
        )
        log.info("wrap_usdce_to_pusd_result", amount=amount, ok=ok)
        return ok
    except Exception:
        log.exception("wrap_usdce_to_pusd_error", amount=amount)
        return False


# ── Read-only self-check CLI (python -m quoter.chain.positions_ops --check) ──

_COLLATERAL_NAMES = {USDCE_ADDRESS: "USDC.e", PUSD_ADDRESS: "pUSD"}


def _check_relayer_nonce() -> tuple[bool, str]:
    """GET {relayer}/nonce for the signer EOA, attaching key-auth headers when
    configured. Read-only; proves relayer reachability + address derivation."""
    private_key = os.environ.get("POLY_PRIVATE_KEY", "").strip()
    if not private_key:
        return False, "skipped (POLY_PRIVATE_KEY missing)"
    from eth_account import Account  # lazy: keeps module import-safe without it

    signer = Account.from_key(private_key).address
    tx_type = "SAFE" if _sig_type() == 2 else "PROXY"
    key_headers = _relayer_key_headers()
    resp = httpx.get(
        f"{_relayer_base_url()}/nonce",
        params={"address": signer, "type": tx_type},
        headers=key_headers or {},
        timeout=_RPC_TIMEOUT_S,
    )
    auth = "key-auth headers" if key_headers is not None else "no auth headers"
    try:
        body = resp.json()
    except ValueError:
        body = None
    ok = resp.status_code == 200 and isinstance(body, dict) and body.get("nonce") is not None
    return ok, f"HTTP {resp.status_code}, {auth}, nonce {'present' if ok else 'absent'}"


def _check_collateral(condition_id: str) -> tuple[bool, str]:
    """Read the proxy's pair balances for both collateral candidates (eth_call only)."""
    proxy = _proxy_address()
    if proxy is None:
        return False, "skipped (POLY_FUNDER_ADDRESS missing)"
    parts: list[str] = []
    detected: str | None = None
    for collateral in _COLLATERAL_CANDIDATES:
        up, down = _pair_balances(proxy, collateral, condition_id)
        parts.append(f"{_COLLATERAL_NAMES[collateral]} up={up} down={down}")
        if detected is None and (up > 0 or down > 0):
            detected = _COLLATERAL_NAMES[collateral]
    verdict = f"detected {detected}" if detected else "no position held (ops would no-op)"
    return True, f"{verdict}; units: {', '.join(parts)}"


def _check_usdce_balance() -> tuple[bool, str]:
    """Proxy's on-chain USDC.e balance — what the wrap sweeper would convert."""
    if _proxy_address() is None:
        return False, "skipped (POLY_FUNDER_ADDRESS missing)"
    bal = usdce_balance()  # never raises
    if bal is None:
        return False, "read failed"
    return True, f"${bal:.2f}"


def _run_check(condition_id: str | None) -> int:
    """Run all read-only checks, print PASS/FAIL lines. NEVER sends a transaction.

    Prints env var NAMES and presence only — values are never printed.
    """
    try:  # convenience: pick up the server/operator .env like the runbooks do
        from dotenv import load_dotenv

        load_dotenv(".env")
    except ImportError:
        pass

    exit_code = 0

    def line(name: str, ok: bool, detail: str) -> None:
        nonlocal exit_code
        if not ok:
            exit_code = 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    for env_name in ("POLY_PRIVATE_KEY", "POLY_FUNDER_ADDRESS"):
        present = bool(os.environ.get(env_name, "").strip())
        line(f"env {env_name}", present, "present" if present else "missing")

    if _relayer_key_auth_present():
        mode = "key-auth (POLY_RELAYER_API_KEY + POLY_RELAYER_API_KEY_ADDRESS)"
    elif _relayer_creds_present():
        mode = "hmac (POLY_BUILDER_API_KEY/SECRET/PASSPHRASE)"
    else:
        mode = "none -> Path B direct on-chain (web3, EOA pays gas)"
    line("relayer auth mode", True, mode)

    try:
        ok, detail = _check_relayer_nonce()
    except Exception as exc:  # network/parse errors must not print secrets
        ok, detail = False, f"error ({type(exc).__name__})"
    line("relayer reachability (GET /nonce)", ok, detail)

    ok, detail = _check_usdce_balance()
    line("proxy USDC.e balance (wrap candidate)", ok, detail)

    if condition_id is not None:
        try:
            ok, detail = _check_collateral(condition_id)
        except Exception as exc:
            ok, detail = False, f"error ({type(exc).__name__})"
        line(f"collateral detection {condition_id}", ok, detail)

    print("self-check complete — no transactions were sent")
    return exit_code


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m quoter.chain.positions_ops",
        description="Read-only self-check for the positions-ops relayer/chain setup. "
        "Never sends transactions; never prints env values.",
    )
    parser.add_argument("--check", action="store_true", help="run the read-only self-check")
    parser.add_argument(
        "condition_id",
        nargs="?",
        default=None,
        help="optional condition id (0x…, 32 bytes) for collateral detection",
    )
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("nothing to do: pass --check (this CLI is read-only by design)")
    return _run_check(args.condition_id)


if __name__ == "__main__":
    raise SystemExit(_main())

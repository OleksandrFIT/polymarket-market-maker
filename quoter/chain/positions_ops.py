"""On-chain position ops for the proxy wallet: merge matched pairs -> collateral, redeem resolved.

Chosen path documented in docs/superpowers/specs/2026-07-02-positions-ops-spike-notes.md.

Two execution paths, selected automatically:

* Path A (preferred, gasless): the official Polymarket relayer
  (``py-builder-relayer-client``) executes the call AS the proxy wallet, gas paid
  by Polymarket. Requires a Relayer API key (Polymarket web UI: Settings > API Keys)
  in env ``POLY_BUILDER_API_KEY`` / ``POLY_BUILDER_SECRET`` / ``POLY_BUILDER_PASSPHRASE``.
* Path B (fallback, direct): the owner EOA sends the tx itself and pays POL gas
  (~$0.01/tx; keep ~1-2 POL on the EOA). For ``POLY_SIGNATURE_TYPE=1`` the EOA calls
  ``ProxyWalletFactory.proxy(calls)`` which routes through the EOA's proxy wallet;
  for sig type 0 the EOA calls the CTF directly. Sig type 2 (Gnosis Safe) is only
  supported via Path A.

Env consumed (names only — values are NEVER logged by this module):
    POLY_PRIVATE_KEY        owner EOA key (signs relayer request / direct tx)
    POLY_FUNDER_ADDRESS     proxy wallet address holding the positions
    POLY_SIGNATURE_TYPE     0 = EOA, 1 = Polymarket proxy, 2 = Gnosis Safe (default 1)
    POLY_BUILDER_API_KEY    \\
    POLY_BUILDER_SECRET      } Path A relayer HMAC creds (optional; enables gasless)
    POLY_BUILDER_PASSPHRASE /
    POLY_RELAYER_URL        relayer host (default https://relayer-v2.polymarket.com)
    POLY_RPC_URL            Polygon JSON-RPC (default https://polygon-rpc.com)

Contract addresses (verified on polygonscan 2026-07-02, see spike notes):
    ConditionalTokens  0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
    pUSD (v2 collat.)  0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB
    USDC.e (legacy)    0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
    ProxyWalletFactory 0xaB45c5A4B0c941a2F231C04C3f49182e1A254052

Collateral is auto-detected per condition (USDC.e first, then pUSD) by reading the
proxy's ERC-1155 pair balances — position ids depend on the collateral token, so
passing the wrong one would revert. Verified 2026-07-02: live 5m market CLOB token
ids equal the USDC.e-derived CTF position ids.

NEVER places orders. ``merge_pairs`` never burns more than the passed qty and
refuses when the wallet holds less. No token approvals are made (merge/redeem
burn the caller's own ERC-1155 balance — no approval needed).

Import-safe without web3: web3 and the relayer client are imported lazily inside
the functions that need them. Read-only chain queries use eth_abi + httpx only
(both already project deps).
"""

from __future__ import annotations

import os

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


def _encode_factory_proxy_call(inner_calldata: bytes) -> bytes:
    """calldata for ProxyWalletFactory.proxy([(Call=1, CTF, 0, inner)]) — Path B, sig type 1."""
    return _selector("proxy((uint8,address,uint256,bytes)[])") + abi_encode(
        ["(uint8,address,uint256,bytes)[]"],
        [[(1, to_checksum_address(CTF_ADDRESS), 0, inner_calldata)]],
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


def _execute_via_relayer(calldata: bytes) -> bool:
    """Execute ``calldata`` against the CTF as the proxy wallet via the Polymarket
    relayer (gasless). Requires relayer API creds + POLY_PRIVATE_KEY."""
    from py_builder_relayer_client.client import (  # type: ignore[import-untyped]
        RelayClient,  # lazy: optional dep
    )
    from py_builder_relayer_client.models import (  # type: ignore[import-untyped]
        RelayerTxType,
        Transaction,
    )
    from py_builder_signing_sdk.config import BuilderConfig  # type: ignore[import-untyped]
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds  # type: ignore[import-untyped]

    sig_type = _sig_type()
    if sig_type == 1:
        tx_type = RelayerTxType.PROXY
    elif sig_type == 2:
        tx_type = RelayerTxType.SAFE
    else:
        log.warning("relayer_unsupported_sig_type", sig_type=sig_type)
        return False

    client = RelayClient(
        relayer_url=os.environ.get("POLY_RELAYER_URL", "").strip() or DEFAULT_RELAYER_URL,
        chain_id=CHAIN_ID,
        private_key=os.environ["POLY_PRIVATE_KEY"].strip(),
        builder_config=BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=os.environ["POLY_BUILDER_API_KEY"].strip(),
                secret=os.environ["POLY_BUILDER_SECRET"].strip(),
                passphrase=os.environ["POLY_BUILDER_PASSPHRASE"].strip(),
            )
        ),
        relay_tx_type=tx_type,
        rpc_url=_rpc_url(),
    )
    resp = client.execute(
        [Transaction(to=to_checksum_address(CTF_ADDRESS), data="0x" + calldata.hex(), value="0")],
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


def _execute_direct(calldata: bytes) -> bool:
    """EOA sends the tx itself. sig type 1 -> factory.proxy(...); 0 -> CTF directly."""
    from web3 import Web3  # lazy: module stays importable without web3

    sig_type = _sig_type()
    if sig_type == 1:
        to, data = PROXY_WALLET_FACTORY, _encode_factory_proxy_call(calldata)
    elif sig_type == 0:
        to, data = CTF_ADDRESS, calldata
    else:
        log.warning("direct_path_unsupported_sig_type", sig_type=sig_type, hint="use relayer")
        return False

    w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": _RPC_TIMEOUT_S}))
    acct = w3.eth.account.from_key(os.environ["POLY_PRIVATE_KEY"].strip())
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
    return ok


def _execute(calldata: bytes) -> bool:
    """Route to relayer (gasless) when creds are configured, else direct on-chain."""
    if not os.environ.get("POLY_PRIVATE_KEY", "").strip():
        log.error("positions_ops_missing_private_key_env")
        return False
    if _relayer_creds_present():
        return _execute_via_relayer(calldata)
    return _execute_direct(calldata)


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
        ok = _execute(encode_merge(collateral, condition_id, units))
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
        ok = _execute(encode_redeem(collateral, condition_id))
        log.info("redeem_result", condition_id=condition_id, ok=ok)
        return ok
    except Exception:
        log.exception("redeem_error", condition_id=condition_id)
        return False

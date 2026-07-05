"""wrap_usdce_to_pusd: calldata encoding + refusal gates (no network, no txs).

The wrap mechanism itself (permissionless wrapper contract, role-gated pUSD.wrap)
was verified on-chain 2026-07-05 — see docs/superpowers/specs/
2026-07-02-positions-ops-spike-notes.md. These tests pin the exact call encoding
and the safety gates around it.
"""

from __future__ import annotations

from eth_abi.abi import decode as abi_decode

from quoter.chain import positions_ops as po

PROXY = "0x00000000000000000000000000000000DeaDBeef"


# ── encoding ──


def test_encode_erc20_approve_calldata():
    data = po.encode_erc20_approve(po.PUSD_WRAPPER_ADDRESS, 10_000_000)
    assert data[:4].hex() == "095ea7b3"  # approve(address,uint256)
    spender, amount = abi_decode(["address", "uint256"], data[4:])
    assert spender.lower() == po.PUSD_WRAPPER_ADDRESS.lower()
    assert amount == 10_000_000


def test_encode_wrap_calldata():
    data = po.encode_wrap(po.USDCE_ADDRESS, PROXY, 10_000_000)
    assert data[:4].hex() == "62355638"  # wrap(address,address,uint256)
    token, to, amount = abi_decode(["address", "address", "uint256"], data[4:])
    assert token.lower() == po.USDCE_ADDRESS.lower()
    assert to.lower() == PROXY.lower()
    assert amount == 10_000_000


def test_factory_proxy_call_batches_multiple_calls():
    calls = [
        (po.USDCE_ADDRESS, po.encode_erc20_approve(po.PUSD_WRAPPER_ADDRESS, 7)),
        (po.PUSD_WRAPPER_ADDRESS, po.encode_wrap(po.USDCE_ADDRESS, PROXY, 7)),
    ]
    data = po._encode_factory_proxy_call(calls)
    assert data[:4] == po._selector("proxy((uint8,address,uint256,bytes)[])")
    (decoded,) = abi_decode(["(uint8,address,uint256,bytes)[]"], data[4:])
    assert len(decoded) == 2
    for (typ, to, value, inner), (want_to, want_data) in zip(decoded, calls, strict=True):
        assert typ == 1 and value == 0
        assert to.lower() == want_to.lower()
        assert inner == want_data


# ── gates (all offline; _execute must never be reached unless asserted) ──


def _no_execute(calls):  # pragma: no cover - failing guard
    raise AssertionError("_execute must not be called")


def test_wrap_refuses_nonpositive_amount(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER_ADDRESS", PROXY)
    monkeypatch.setattr(po, "_execute", _no_execute)
    assert po.wrap_usdce_to_pusd(0.0) is False
    assert po.wrap_usdce_to_pusd(-3.0) is False


def test_wrap_refuses_missing_funder(monkeypatch):
    monkeypatch.delenv("POLY_FUNDER_ADDRESS", raising=False)
    monkeypatch.setattr(po, "_execute", _no_execute)
    assert po.wrap_usdce_to_pusd(10.0) is False


def test_wrap_refuses_when_balance_below_amount(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER_ADDRESS", PROXY)
    monkeypatch.setattr(po, "_erc20_balance_units", lambda token, owner: 9_999_999)
    monkeypatch.setattr(po, "_execute", _no_execute)
    assert po.wrap_usdce_to_pusd(10.0) is False


def test_wrap_executes_approve_then_wrap_as_one_batch(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER_ADDRESS", PROXY)
    monkeypatch.setattr(po, "_erc20_balance_units", lambda token, owner: 10_000_000)
    seen: list[list[tuple[str, bytes]]] = []

    def fake_execute(calls):
        seen.append(calls)
        return True

    monkeypatch.setattr(po, "_execute", fake_execute)
    assert po.wrap_usdce_to_pusd(10.0) is True
    assert len(seen) == 1  # ONE batched proxy tx
    (approve_call, wrap_call) = seen[0]
    assert approve_call == (
        po.USDCE_ADDRESS,
        po.encode_erc20_approve(po.PUSD_WRAPPER_ADDRESS, 10_000_000),
    )
    assert wrap_call == (
        po.PUSD_WRAPPER_ADDRESS,
        po.encode_wrap(po.USDCE_ADDRESS, PROXY, 10_000_000),
    )


def test_wrap_returns_false_when_execute_fails(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER_ADDRESS", PROXY)
    monkeypatch.setattr(po, "_erc20_balance_units", lambda token, owner: 20_000_000)
    monkeypatch.setattr(po, "_execute", lambda calls: False)
    assert po.wrap_usdce_to_pusd(10.0) is False


def test_wrap_never_raises(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER_ADDRESS", PROXY)

    def boom(token, owner):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(po, "_erc20_balance_units", boom)
    assert po.wrap_usdce_to_pusd(10.0) is False


def test_usdce_balance_none_without_funder(monkeypatch):
    monkeypatch.delenv("POLY_FUNDER_ADDRESS", raising=False)
    assert po.usdce_balance() is None


def test_usdce_balance_none_on_rpc_error(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER_ADDRESS", PROXY)

    def boom(token, owner):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(po, "_erc20_balance_units", boom)
    assert po.usdce_balance() is None


# ── wrap_decision: the sweeper's gate as a pure, loggable function ──


def test_wrap_decision_reasons():
    # None balance -> never wrap, distinct reason (was silently skipped, stranding $25)
    assert po.wrap_decision(None, 5.0) == (False, "balance_unknown")
    # boundary: exactly at min is NOT > min -> below_min (matches old `bal > WRAP_MIN`)
    assert po.wrap_decision(5.0, 5.0) == (False, "below_min")
    assert po.wrap_decision(4.99, 5.0) == (False, "below_min")
    # strictly above min -> wrap
    assert po.wrap_decision(25.0, 5.0) == (True, "wrap")


# ── one-off wrap CLI (the ONLY tx-sending path; preview unless --yes) ──


def _wrap_must_not_run(amount):  # pragma: no cover - failing guard
    raise AssertionError("wrap_usdce_to_pusd must not be called")


def test_cli_wrap_preview_sends_nothing(monkeypatch, capsys):
    monkeypatch.setattr(po, "usdce_balance", lambda: 25.0)
    monkeypatch.setattr(po, "wrap_usdce_to_pusd", _wrap_must_not_run)
    assert po._main(["--wrap"]) == 0                 # preview, no --yes
    assert "preview only" in capsys.readouterr().out


def test_cli_wrap_yes_executes_full_balance(monkeypatch):
    monkeypatch.setattr(po, "usdce_balance", lambda: 25.0)
    seen: list[float] = []
    monkeypatch.setattr(po, "wrap_usdce_to_pusd", lambda amt: seen.append(amt) or True)
    assert po._main(["--wrap", "--yes"]) == 0
    assert seen == [25.0]                            # default = full balance


def test_cli_wrap_amount_respected(monkeypatch):
    monkeypatch.setattr(po, "usdce_balance", lambda: 25.0)
    seen: list[float] = []
    monkeypatch.setattr(po, "wrap_usdce_to_pusd", lambda amt: seen.append(amt) or True)
    assert po._main(["--wrap", "--amount", "10", "--yes"]) == 0
    assert seen == [10.0]


def test_cli_wrap_rejects_amount_over_balance(monkeypatch):
    monkeypatch.setattr(po, "usdce_balance", lambda: 5.0)
    monkeypatch.setattr(po, "wrap_usdce_to_pusd", _wrap_must_not_run)
    assert po._main(["--wrap", "--amount", "10", "--yes"]) == 1   # refuse, no tx


def test_cli_wrap_aborts_on_balance_read_fail(monkeypatch):
    monkeypatch.setattr(po, "usdce_balance", lambda: None)
    monkeypatch.setattr(po, "wrap_usdce_to_pusd", _wrap_must_not_run)
    assert po._main(["--wrap", "--yes"]) == 1

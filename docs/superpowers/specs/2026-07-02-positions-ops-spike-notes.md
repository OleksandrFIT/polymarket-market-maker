# Spike notes: positions_ops — merge/redeem as the proxy wallet (2026-07-02)

Deliverable of Task 5, plan `docs/superpowers/plans/2026-07-02-top-book-live-mm.md`.
Module: `quoter/chain/positions_ops.py` — `merge_pairs(condition_id, qty) -> bool`,
`redeem(condition_id) -> bool`. **No real transaction has been run** (acceptance is
operator-gated, see procedure at the bottom).

## Chosen path: A (gasless relayer) primary, B (direct on-chain) automatic fallback

The module picks the path at call time (auth resolution order, updated 2026-07-02):

- **Path A, key auth (NEW scheme, preferred)** when `POLY_RELAYER_API_KEY` +
  `POLY_RELAYER_API_KEY_ADDRESS` are BOTH set: executes via the official Polymarket
  relayer authenticating with plain HTTP headers `RELAYER_API_KEY` /
  `RELAYER_API_KEY_ADDRESS` (this is what the operator's UI-created key provides).
- **Path A, HMAC (old scheme)** else, when `POLY_BUILDER_API_KEY` /
  `POLY_BUILDER_SECRET` / `POLY_BUILDER_PASSPHRASE` are set: signed
  `POLY_BUILDER_*` headers via `py-builder-signing-sdk` (stock client behavior).
- **Path B** otherwise: the owner EOA (`POLY_PRIVATE_KEY`) sends the tx itself via
  `web3==7.16.0` and pays POL gas. For `POLY_SIGNATURE_TYPE=1` it calls
  `ProxyWalletFactory.proxy(calls)` which executes through the EOA's proxy wallet;
  sig type 0 calls the CTF directly; sig type 2 (Gnosis Safe) is Path-A-only
  (Safe `execTransaction` plumbing not implemented in the spike).

Why A first: zero gas prerequisites, official client, supports both PROXY and SAFE
wallet types. Why keep B: Path A needs a Relayer API key the operator must create in
the Polymarket UI; B works today with only ~1-2 POL on the EOA and is ~30 lines.

## Investigation findings

### Installed CLOB client has no merge/redeem/relayer support

`py_clob_client_v2` 1.0.1 (and legacy `py_clob_client` 0.34.6) contain no
relayer/proxy-exec/merge/redeem helpers — only `neg_risk` flags for order building
and contract addresses in `config.py`. A separate library was required.

### Path A: Polymarket relayer (verified from source of the official client)

- Docs: docs.polymarket.com "Gasless Transactions" (`developers/builders/relayer-client`).
- Relayer host: `https://relayer-v2.polymarket.com`.
- Auth — the relayer accepts EITHER scheme (verified via official docs 2026-07-02):
  - **NEW key auth**: headers `RELAYER_API_KEY` + `RELAYER_API_KEY_ADDRESS` — the
    key created in the Polymarket web UI under **Settings > API Keys** (what the
    operator has). Env: `POLY_RELAYER_API_KEY` / `POLY_RELAYER_API_KEY_ADDRESS`.
  - **OLD HMAC builder auth**: signed `POLY_BUILDER_*` headers via
    `py-builder-signing-sdk` (`BuilderConfig` + `BuilderApiKeyCreds`). Env:
    `POLY_BUILDER_API_KEY` / `POLY_BUILDER_SECRET` / `POLY_BUILDER_PASSPHRASE`.
  There is no keyless relayer POST access (GET endpoints like `/nonce`,
  `/transaction` respond without auth).
- Key-auth implementation note: `py-builder-relayer-client==0.0.2` is the LATEST
  on PyPI (checked 2026-07-02) and is HMAC-only. Its auth headers are generated in
  exactly one place — `RelayClient._post_request` → `_generate_builder_headers`
  (GETs are sent unauthenticated). The module therefore subclasses `RelayClient`
  (`_KeyAuthRelayClient` inside `_build_relay_client`): overrides `_post_request`
  to attach the two key-auth headers and no-ops `assert_builder_creds_needed`;
  ALL payload construction/signing (nonce/relay-payload fetch, proxy calldata
  encoding, EOA signature, polling) is inherited unchanged. Revisit if a newer
  client version adds native `relayerApiKey` support.
- Wallet types: `RelayerTxType.PROXY` (sig type 1; wallet auto-deploys on first tx)
  and `RelayerTxType.SAFE` (sig type 2; must already be deployed — ours is).
- Covered ops per docs: wallet deploy, approvals, **CTF split/merge/redeem**, transfers.
- Flow (PROXY): client fetches `/relay-payload` (nonce + relay address), encodes
  `proxy((uint8,address,uint256,bytes)[])` calldata, EOA signs the relay request,
  POSTs to `/submit-transaction` with HMAC headers; `response.wait()` polls until
  `STATE_MINED/STATE_CONFIRMED` (returns `None` on `STATE_FAILED`).
- NOT verified end-to-end: an actual relayed tx (needs the operator-created key).

### Path B: direct on-chain (all addresses verified on polygonscan 2026-07-02)

| Contract | Address | Verification |
|---|---|---|
| ConditionalTokens (CTF) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | polygonscan: "Polymarket: Conditional Tokens", source verified (exact match) |
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | canonical bridged USDC; **proven the live collateral, see below** |
| pUSD (Polymarket USD) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | polygonscan token page: "Polymarket USD (pUSD)", 6 decimals; listed as v2 collateral in `py_clob_client_v2.config` |
| ProxyWalletFactory (sig type 1) | `0xaB45c5A4B0c941a2F231C04C3f49182e1A254052` | polygonscan: verified, `proxy((uint8 typeCode, address to, uint256 value, bytes data)[]) payable`; same address in the official relayer client config |

Calls (encoded with `eth_abi`, selectors computed from the verified CTF ABI —
`mergePositions` = `0x9e7212ad`, `redeemPositions` = `0x01b7037c`):

- merge: `CTF.mergePositions(collateral, 0x00..0, conditionId, [1,2], qty*1e6)`
- redeem: `CTF.redeemPositions(collateral, 0x00..0, conditionId, [1,2])`

No token approvals needed (both burn the caller's own ERC-1155 balance); the module
makes none and never places orders.

### Collateral finding (important)

Although the v2 CLOB client config lists **pUSD** as chain-137 collateral, a live
read-only check (eth_call, 2026-07-02) proved the bot's 5m markets are
**USDC.e-collateralized at the CTF level**: for market/condition
`0xf164ee996b6eb2f527e28f6f48a9cb75db68d32e1ae5235a227c6c8699c99637` (ETH 5m), the
CLOB token ids stored in `state.db` equal `CTF.getPositionId(USDC.e, collectionId)`
for index sets 1/2 — and do NOT match the pUSD derivation. pUSD is an
exchange-level wrapper. The module therefore **auto-detects collateral per
condition** (USDC.e first, then pUSD) by reading the proxy's pair balances, so a
future migration to pUSD-collateralized conditions keeps working.

### Read-only verifications performed (no transactions)

- `_position_id` (CTF `getCollectionId` + `getPositionId` via eth_call — collection id
  needs alt-bn128 math, cannot be a local keccak): matches real CLOB token ids. ✔
- `_payout_denominator`: returns 1 for the resolved test market (guards `redeem`
  against unresolved conditions). ✔
- `_pair_balances` for `POLY_FUNDER_ADDRESS`: reads fine (0/0 on that market;
  `_detect_collateral` correctly returns None → ops no-op with `False`). ✔
- Import-safety: module imports cleanly with `web3` / relayer client blocked. ✔
- `ruff` and strict `mypy` clean.

### Environment / dependency notes

- Installed into `.venv` (uv): `web3==7.16.0`, `py-builder-relayer-client==0.0.2`
  (+ `py-builder-signing-sdk` dep). `pyproject.toml` NOT updated (out of this task's
  file scope) — on the server run
  `uv pip install --python .venv/bin/python web3 py-builder-relayer-client` before
  using Path B / Path A respectively; the module itself imports them lazily and the
  rest of the bot runs without them. Side effect: web3 pins `websockets<16` →
  **websockets downgraded 16.0 → 15.0.1** (pyproject requires `>=12.0`; full test
  suite green after the change).
- `https://polygon-rpc.com` now returns 401 → default RPC switched to
  `https://polygon.drpc.org` (Polymarket's own relayer client default). Override
  with `POLY_RPC_URL`.
- Env consumed by the module (names only, values never logged):
  `POLY_PRIVATE_KEY`, `POLY_FUNDER_ADDRESS`, `POLY_SIGNATURE_TYPE`,
  `POLY_RELAYER_API_KEY`, `POLY_RELAYER_API_KEY_ADDRESS` (new key auth),
  `POLY_BUILDER_API_KEY`, `POLY_BUILDER_SECRET`, `POLY_BUILDER_PASSPHRASE` (HMAC),
  `POLY_RELAYER_URL`, `POLY_RPC_URL`.

## Read-only self-check CLI (`--check`)

```bash
.venv/bin/python -m quoter.chain.positions_ops --check [0x<condition_id>]
```

Sends NO transactions, prints env var NAMES only (never values), loads `./.env`
if present. One `PASS`/`FAIL` line per check, exit code 1 if any check failed:

```
PASS  env POLY_PRIVATE_KEY: present
PASS  env POLY_FUNDER_ADDRESS: present
PASS  relayer auth mode: key-auth (POLY_RELAYER_API_KEY + POLY_RELAYER_API_KEY_ADDRESS)
PASS  relayer reachability (GET /nonce): HTTP 200, key-auth headers, nonce present
PASS  collateral detection 0x…: detected USDC.e; units: USDC.e up=… down=…, pUSD up=0 down=0
self-check complete — no transactions were sent
```

- *auth mode* shows which scheme resolves: `key-auth` > `hmac` > `none -> Path B`.
- *reachability* derives the signer address from `POLY_PRIVATE_KEY` and GETs
  `{relayer}/nonce?address=…&type=PROXY|SAFE` with the key-auth headers attached
  when configured.
- *collateral detection* (only with the optional condition-id arg) reads the
  proxy's pair balances for both collateral candidates via `eth_call`.

## Gas requirements

- **Path A: none.** Relayer sponsors gas; EOA signs off-chain only.
- **Path B:** EOA pays POL. merge/redeem via factory ≈ 150-300k gas; at Polygon's
  ~30-100 gwei that is well under $0.01-0.05 per tx. Keep **~1-2 POL** on the EOA
  (plan's "$1-2" of POL) for many operations.

## Caveats / not covered by the spike

- Neg-risk markets are NOT supported (they need the NegRiskAdapter
  `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`); the 5m Up/Down markets are plain
  binary CTF conditions, so this is out of scope here.
- Path A untested end-to-end (submit-tx POST needs the operator's key; GET-side
  reachability with the key-auth headers verified via `--check`).
- Direct path for `POLY_SIGNATURE_TYPE=2` (Safe) intentionally unimplemented.
- Functions are blocking (httpx/web3 sync) — call them off the quoting hot path
  (e.g. from the merge_runner sweeper via a thread executor).

## Manual acceptance procedure (operator-gated — NOT yet run)

Goal: one ~$1 pair merge on the live proxy wallet. Bounded loss: $0 (merge is
value-neutral: burns 1 Up + 1 Down, credits $1.00 USDC.e).

1. Pick the path:
   - **Path A, key auth (preferred — what the operator has):** log into
     polymarket.com with the bot account → Settings > API Keys → create a
     **Relayer API key**; put the key and its address into the server `.env` as
     `POLY_RELAYER_API_KEY` and `POLY_RELAYER_API_KEY_ADDRESS`.
   - **Path A, HMAC (legacy alternative):** set `POLY_BUILDER_API_KEY`,
     `POLY_BUILDER_SECRET`, `POLY_BUILDER_PASSPHRASE` instead.
   - **Path B:** leave all five unset; send ~1-2 POL to the owner EOA address.
2. Obtain a matched pair: if the proxy already holds ≥1 share of BOTH sides of some
   condition, use it. Otherwise buy 1 share Up AND 1 share Down (~$1 total) in a
   current market via the UI. Note the market's `condition_id` (bot log,
   `state.db` `markets.market_id`, or gamma-api).
3. Preflight (read-only, no transactions):
   `.venv/bin/python -m quoter.chain.positions_ops --check 0x<condition_id>`
   → expect all `PASS`: auth mode matching step 1 (`key-auth` for the preferred
   path), relayer `HTTP 200, nonce present`, and the collateral line showing the
   pair you are about to merge (`detected USDC.e; … up>0 down>0`).
4. Record the proxy's USDC.e balance before (Polymarket UI cash, or polygonscan
   token balance of the funder address).
5. On the server, from the repo root:

   ```bash
   .venv/bin/python -c "
   from dotenv import load_dotenv; load_dotenv('.env')
   from quoter.chain.positions_ops import merge_pairs
   print(merge_pairs('0x<condition_id>', 1))"
   ```

   Expected: `True`, with a `relayer_auth_mode` line (`mode=key` or `mode=hmac`)
   followed by `relayer_tx_done` (Path A) — or `direct_tx_done` (Path B) — a log
   line containing the tx hash.
6. Verify: USDC.e balance +$1.00; both legs of the position gone (UI portfolio or
   polygonscan ERC-1155). **Record the tx hash below.**
7. Optional redeem check: after any market resolves while the proxy holds the
   winning side, run `redeem('0x<condition_id>')` → `True`, winnings credited.

Acceptance record (fill on completion):

- [ ] merge tx hash: `________________`
- [ ] path used: A-key / A-hmac / B
- [ ] balance delta confirmed: `________`

## Status

DONE_WITH_CONCERNS — implementation + read-only verification complete (incl.
key-auth: `--check` against the live relayer returns HTTP 200 with the
`RELAYER_API_KEY` headers attached); the single $1 acceptance merge awaits
operator authorization with the operator's UI-created Relayer API key
(`POLY_RELAYER_API_KEY` / `POLY_RELAYER_API_KEY_ADDRESS`). Key-auth POST
`/submit` is NOT yet exercised end-to-end — that is exactly what the acceptance
merge validates.

## ACCEPTANCE PASSED (2026-07-02, operator-approved step A)

Real relayer transaction executed from the server wallet via the NEW key-auth path:
- auth mode: `key` (`POLY_RELAYER_API_KEY` headers)
- op: `redeem(0x80f086f7024999b5590eba4a4947a75f152e046322ab0819208716e5f8242bcd)`
  (btc-updown-5m-1774036200, 1000 worthless Down shares, value $0.00 — zero-risk probe)
- relayer transaction id: 019f23c6-cdef-7bae-a5d4-4e0f8fa382d4
- tx hash: 0x767cdbde5c2e76e424780a9637b0c29112d7c76be027bf5979a83c5bed6c57c6 (MINED/CONFIRMED, gasless)
- post-check: position gone from wallet (31 redeemable left)

This proves the full Path A chain end-to-end (key-auth -> POST /submit -> proxy exec ->
confirmation polling). merge_pairs uses the identical transport with different calldata;
its first natural pair on the attended live test serves as the merge acceptance.

## Addendum 2026-07-05: USDC.e -> pUSD wrap (`wrap_usdce_to_pusd`)

Problem (verified live): the bot BUYS with pUSD, but `merge_pairs`/`redeem` return
collateral in **USDC.e** — merge proceeds strand on the proxy and never re-enter the
trading balance ($10 USDC.e stranded at the time of the spike).

### Wrap mechanism found (on-chain investigation, 2026-07-05)

- **pUSD `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` is an EIP-1967 UUPS proxy**
  (impl `0x6bbcef9f7ef3b6c592c99e0f206a0de94ad0925f`), name "Polymarket USD",
  6 decimals, Solady OwnableRoles. It is NOT a WETH-style self-serve wrapper:
  no `deposit`/`depositFor`. It exposes `wrap(address,address,uint256,address,bytes)`
  (`0xb97b57c7`) / `unwrap(...)` (`0xd600875d`) plus `addWrapper`/`removeWrapper`
  and `addMinter`/`removeMinter` — **`pUSD.wrap` is role-gated**: eth_call from a
  non-wrapper reverts Solady `Unauthorized()` (`0x82b42900`). Constants:
  `USDCE()` = USDC.e, `USDC()` = native USDC `0x3c499c...`, `VAULT()` =
  `0xC417fD8E9661c0d2120B64a04Bb3278C17E99DB1` (receives the wrapped USDC.e).
- **The public conversion path is a separate permissionless wrapper contract**
  `0x93070a847efEf7F70739046A929D47a521F5B8ee` (holds wrapper role 2 on pUSD,
  same owner `0x47ebfac3...` as pUSD):
  **`wrap(address token, address to, uint256 amount)` — selector `0x62355638`.**
  It `transferFrom`s the caller's USDC.e into the pUSD contract, which mints pUSD
  1:1 to `to` and forwards the USDC.e to the VAULT. Permissionless: eth_call
  `wrap(USDC.e, to, 0)` from an arbitrary address succeeds (a role gate would
  revert `Unauthorized` before the transfer; the only failure mode observed is
  `TransferFromFailed` `0x7939f424` when balance/allowance are missing).
- **This is exactly what the UI/user wallets do.** Live evidence, tx
  `0xacd202c41698720d3e489994eb89528d5b4c809fbf530c3c3eeef072c111e3ec`: user proxy
  calls `wrap(USDC.e, self, 12975200)` on `0x93070a84...` (with a standing USDC.e
  approval to the wrapper) → USDC.e proxy→pUSD→VAULT, pUSD minted 1:1 to the proxy,
  pUSD `Wrapped`-style event `0xc00a5c84...` (caller=wrapper, token=USDC.e,
  to=proxy, amount). In a ~1h window, 930 wrap events, all token=USDC.e, via 5
  registered wrapper contracts — `0x93070a84...` (plain wrap, most used, 539) and
  four `0xada...` CTF-ops adapters (split/merge/redeem/convertPositions with
  built-in conversion, e.g. `0xada2005600dec949baf300f4c6120000bdb6eaab`).
- `py-builder-relayer-client` 0.0.2 has **no** dedicated convert/wrap helper (its
  "deposit wallet" API is the onboarding deposit-address feature, unrelated).

### Implementation

`wrap_usdce_to_pusd(amount: float) -> bool` in `quoter/chain/positions_ops.py`:

- Refuses when the proxy's on-chain USDC.e balance < amount (read-only
  `balanceOf` eth_call first).
- Executes AS the proxy via the same `_execute` transport as merge/redeem
  (relayer key-auth > HMAC > direct), as **ONE batched proxy tx** of two calls:
  1. `USDC.e.approve(0x93070a84..., amount_units)` — exact amount, no standing
     max-allowance left behind;
  2. `wrapper.wrap(USDC.e, proxy, amount_units)`.
  (`_execute`/`_execute_via_relayer`/`_execute_direct` were generalized from a
  single CTF calldata to `list[(to, calldata)]`; relayer `execute()` takes the
  list natively, Path B sig-1 batches via `factory.proxy(calls)`, sig-0 sends
  sequential EOA txs.)
- Read-only helper `usdce_balance() -> float | None` (never raises) + a
  `proxy USDC.e balance (wrap candidate)` line in `--check`.

Sweeper hook (`quoter/runner/merge_runner.py`, `_redeem_sweeper`): after each
redeem pass, when NOT dry_run and `usdce_balance() > WRAP_MIN_USD` ($5), call
`wrap_usdce_to_pusd(balance)` and log `wrap_result`; exceptions stay inside the
sweeper's existing try/except (never stop quoting).

Tests: `tests/test_positions_wrap.py` (exact calldata, batch order, refusal
gates — offline). **No real wrap transaction executed by this task** — the $10
acceptance wrap runs operator-gated on the server:

```bash
.venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv('.env')
from quoter.chain.positions_ops import usdce_balance, wrap_usdce_to_pusd
bal = usdce_balance(); print('usdce', bal)
print(wrap_usdce_to_pusd(bal))"
```

Expected: `relayer_tx_done` with tx hash; pUSD (UI cash) +$10, USDC.e balance ~0.

Acceptance record (fill on completion):

- [ ] wrap tx hash: `________________`
- [ ] pUSD balance delta confirmed: `________`

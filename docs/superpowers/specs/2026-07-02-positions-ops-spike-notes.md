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

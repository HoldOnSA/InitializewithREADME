# stackapp-indexer

Subscribes to the StackApp program on devnet, decodes its Anchor events and
accounts, serves a queryable view over REST + WebSocket, **and serves the whole
web UI**. One process, one port, no Node.

## Run it

```bash
python -m unittest discover -s tests -t .     # stdlib only for most of it
python -m stackapp_indexer --check-layouts    # borsh round-trip self-test

pip install -e . -e ../sim                    # to serve
python -m stackapp_indexer                    # live devnet
python -m stackapp_indexer --mock             # synthetic feed, no network
```

Then open <http://127.0.0.1:8787>.

`--mock` runs `stackapp_sim.Market` behind the real routes. Same pages, same
WebSocket, same JSON shapes — but no deployed program, no airdrop and no network
needed.

## Configuration

Environment variables, all optional:

| Variable | Default |
|---|---|
| `STACKAPP_PROGRAM_ID` | `Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS` |
| `STACKAPP_RPC_HTTP` | `https://api.devnet.solana.com` |
| `STACKAPP_RPC_WS` | `wss://api.devnet.solana.com` |
| `STACKAPP_HOST` / `STACKAPP_PORT` | `127.0.0.1` / `8787` |
| `STACKAPP_CORS_ORIGINS` | `http://localhost:3000` |
| `STACKAPP_REFRESH_INTERVAL` | `20` seconds |
| `STACKAPP_IDL` | `target/idl/stackapp.json` |

**Mainnet endpoints are rejected at startup.** This is an unaudited prototype;
see the root README.

## Routes

Pages (server-rendered Jinja, in `stackapp_indexer/web/`):

```
GET  /                            every registered token, plus a register-a-token panel
GET  /token/{mint}                pool, your registration, pending marker candidates, holders
GET  /feed                        live event feed
```

JSON API — under `/api` precisely so it cannot shadow the pages above:

```
GET  /api/health                       subscriber state, store stats, known events
GET  /api/tokens                       every registered token, newest first
GET  /api/tokens/{mint}                pool state, deposit vault balance
GET  /api/tokens/{mint}/registrations  registrations by weight
GET  /api/tokens/{mint}/pending        marker-transfer candidates awaiting write_registration
GET  /api/registrations/{owner}        every registration for a wallet
GET  /api/registrations/{mint}/{owner}
GET  /api/feed?limit=&mint=&owner=&kind=
GET  /api/pdas/{mint}?owner=
WS   /ws                               live feed: backlog, then events, newest first
```

Plus the browser helpers the UI uses: `POST /api/tx/{action}` (build an unsigned
message), `POST /api/render-event`, `GET /api/registration-card/{mint}/{owner}`.

`u128` fields (`accRewardPerShare`) are serialised as **strings** — JSON
numbers lose precision past 2^53.

## How it works

Three sources feed one in-memory store:

- **`logsSubscribe`** on the program — the event stream. Low latency, but logs
  can be dropped across a reconnect.
- **`programSubscribe`** plus a periodic `getProgramAccounts` sweep — the
  authoritative account state, which backfills anything the log stream missed. A
  sweep also runs on every reconnect.
- **A periodic sweep of every known `DepositVault`** for marker-transfer
  *candidates* — plain SOL transfers that never mention the program at all, so
  `logsSubscribe` can never see them. This never writes a `Registration`
  itself; it only queues a `PendingRegistration` for the operator (holding
  `GlobalConfig.authority`) to review and sign `write_registration` for. See
  `subscriber.py`'s module docstring and `SECURITY_NOTES.md`.

RPC is plain JSON-RPC over `websockets` / `httpx`, so the wire format is visible
in `subscriber.py` rather than hidden behind a client library.

Anchor discriminators are derived rather than read from an IDL
(`sha256("event:<Name>")[:8]`, `sha256("account:<Name>")[:8]`), so the indexer
works against a deployed program without a build artifact. If
`target/idl/stackapp.json` does exist, `load_idl_overrides()` cross-checks the
hand-written layouts against it at startup and logs any mismatch.

## Why the core is dependency-free

`borsh.py`, `layouts.py`, `pda.py`, `store.py` and `selftest.py` are pure stdlib
— including base58 and the ed25519 on-curve test that `find_program_address`
needs. Only serving requires FastAPI. That is what lets the decode tests run in
any checkout with no install step, which is exactly when you most want them.

One limitation worth naming: the PDA tests check self-consistency
(deterministic, off-curve, reproducible from the bump, real program IDs
correctly identified as on-curve) rather than hardcoded vectors from a reference
implementation. Cross-checking them against `@solana/web3.js` is a good thing to
do once a Node toolchain is available.


## The wallet flow

The UI has no bundler and loads no JavaScript SDK. Transactions are assembled in
Python and signed in the browser - including `register_mint` and
`write_registration`, which are authority-gated on chain but built and signed
exactly like everything else: whichever wallet the operator connects. If
that isn't the real `GlobalConfig.authority`, the transaction just fails on
chain.

```
browser  POST /api/tx/donate {wallet, mint, amount}
server   txbuild.py builds an UNSIGNED legacy transaction message,
         base58-encodes it, and returns it
browser  window.solana.request({method: "signAndSendTransaction",
                                params: {message}})
```

`txbuild.py` implements the legacy message format directly: shortvec lengths,
the three-byte header, account de-duplication and the writable-signers /
readonly-signers / writable / readonly ordering, plus Anchor's
`sha256("global:<name>")[:8]` instruction discriminators.

**The server never sees a private key or a signature.** It produces an unsigned
message; everything else happens inside the wallet extension.

Account order and mutability must match each `#[derive(Accounts)]` struct field
for field, and Solana messages are positional, so a mismatch would be silent
until it failed on chain. `tests/test_txbuild.py::test_account_shape_matches_rust`
parses the Rust and checks every instruction.

## Why the UI is server-rendered

Tailwind and Next.js both need a Node build step. Since the point of this
checkout is that it runs on Python alone, the pages are Jinja templates with
hand-written CSS and about 200 lines of dependency-free JavaScript for the wallet
and the live feed. Everything that needs real arithmetic — durations, SOL
formatting, tenure multipliers, event lines — is computed in `web/format.py`
and `web/events.py`, where `tests/test_web.py` can check it.

One footgun worth knowing about, since it bit twice: this package uses
`from __future__ import annotations`, which turns annotations into strings that
FastAPI resolves against **module** globals. Importing `Request` or `WebSocket`
inside a function leaves them unresolvable, and FastAPI degrades silently — every
page becomes a 422, and the WebSocket handshake 403s. Keep FastAPI imports at
module scope.

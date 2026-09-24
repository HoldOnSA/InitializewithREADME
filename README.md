# StackApp

A **devnet prototype** of a loyalty layer for real pump.fun tokens: holders
register a wallet against a token, and a share of that token's creator fees
flows back to registered holders by live-balance × tenure weight. The token
itself is untouched — real, standard SPL/Token-2022, tradeable anywhere
pump.fun tokens normally trade.

---

## ⚠️ Prototype status — read this first

**This is a prototype. It runs on Solana devnet with test SOL only.**

- No mainnet program deployment, and no mainnet code path. The indexer
  **refuses to start** against a mainnet RPC endpoint (`config.py`,
  `BLOCKED_HOSTS`).
- No real funds, no fiat on-ramp or off-ramp, no exchange integration of any
  kind.
- No custody of user keys, and — unlike an earlier design this repo used to
  describe — **no custody of user tokens either**. StackApp never mints,
  wraps, or holds anyone's pump.fun tokens. A holder's balance is their own,
  ordinary ATA; the program only ever *reads* it.
- **The program has not been audited.** Taking any of this to mainnet would
  require a real third-party security audit *and* a separate legal review
  before it handles a single unit of real user value.

There is one honor-system trust boundary that a mainnet version would need to
resolve — see [Trust model](#trust-model), below — plus a longer list of
findings, verified against real mainnet pump.fun state, in
[`SECURITY_NOTES.md`](SECURITY_NOTES.md). Read that file before this one if
you're auditing the program.

---

## What it does

StackApp does not run a bonding curve, does not tax exits, and does not
issue its own token. A pump.fun mint keeps trading exactly as it always did.
On top of that, StackApp adds an opt-in loyalty pool per mint:

1. A holder registers by sending a small, fixed, non-refundable SOL marker
   (`REGISTRATION_MARKER_LAMPORTS`, 0.0025 SOL) from their own wallet to that
   token's `DepositVault` — a plain transfer, not a program call, so it works
   from any wallet or terminal.
2. The indexer backend observes the marker off chain and calls
   `write_registration`, which stamps `registered_at` and creates that
   wallet's `Registration` PDA.
3. From then on, `weighted_shares = live_balance × tenure_multiplier(now −
   registered_at)` — recomputed from the holder's **real, live SPL token
   balance**, read directly off their own ATA, every time the registration
   is touched (`sync` or `claim`). Nothing here is cached or indexer-reported.
4. Creator fees enter the pool two ways (see
   [`SECURITY_NOTES.md`](SECURITY_NOTES.md) §2 for why there is no automatic
   third): `donate`, fully permissionless, or `pull_pump_fee`, a
   permissionless CPI into pump.fun's own `distribute_creator_fees` — which
   only pays out once a creator has independently added `DepositVault` as a
   shareholder in their token's pump.fun `SharingConfig`.
5. `claim` re-syncs the caller's own weight against their live balance, then
   pays out their share of the pool — real lamports, straight out of
   `DepositVault`.

Two rules make the weighting hard to game:

| Rule | Mechanism |
|---|---|
| Weight always reflects a real, live balance | `sync`/`claim` read the holder's actual ATA on chain every time — never a cached number, never something the indexer can inflate. |
| Sharding a balance across wallets never out-earns holding it in one | Weight is `balance × multiplier(age)` — linear in balance, no per-wallet bonus — and every division floors, so splitting into N wallets can only sum to less than or equal to holding it whole. |

Plus one guard that matters more than it looks: **claims have a minimum
block delay** (`MIN_CLAIM_DELAY_SLOTS`, 4 slots) after a registration's
weight last increased. A wallet cannot buy in, watch a fee land, and claim
against the inflated accumulator in the same block.

The loyalty pool uses the standard O(1) reward-per-share accumulator
(MasterChef / Synthetix `rewardPerTokenStored`). Distributing a fee to every
holder is a single addition. **The program never iterates over holders.**

---

## Layout

```
programs/stackapp/     Anchor program (Rust) + host-side unit tests
  src/math/             pure math: accumulator, tenure weighting
  src/logic.rs          registration/pool state transitions + vault reconciliation
  src/instructions/     one file per instruction
  src/pumpfun.rs        CPI into pump.fun's real distribute_creator_fees
  src/ata.rs            manual token-account reads (legacy SPL Token + Token-2022)
sim/                    Python mirror of the same math, runnable with no toolchain
indexer/                Python indexer + the web UI
  stackapp_indexer/web/   server-rendered pages, no Node anywhere
  stackapp_indexer/txbuild.py  builds unsigned Solana transactions in Python
app/                    optional Next.js + Tailwind frontend (same pages, needs Node)
tests/                  Anchor integration tests (local validator)
SECURITY_NOTES.md        findings from building this design — read alongside this file
```

---

## Running it

### 1. The math and the anti-gaming rules — **no toolchain needed**

`sim/` is a faithful Python mirror of the Rust. Zero dependencies, pure stdlib:

```bash
cd sim && python -m unittest discover -s tests -t .
```

A parity test (`test_parity.py`) parses `programs/stackapp/src/constants.rs`
and the relevant state structs and fails if the two implementations drift
apart, so the mirror cannot quietly stop meaning anything.

For a narrated walk-through of the registration/tenure/fee mechanics with
real numbers:

```bash
cd sim && python scenario.py
```

### 2. The Anchor program

Needs Rust, the Solana CLI and Anchor 0.30.1. On Windows that means WSL.

```bash
cargo test --manifest-path programs/stackapp/Cargo.toml
```

runs the host-side unit tests — the accumulator and the tenure/anti-sharding
math — with no validator and no BPF build.

```bash
anchor build && anchor keys sync && anchor test
```

builds the program and runs the tests under `tests/`. **Note:**
`tests/register_mint.ts` targets the current pump.fun-loyalty design and
passes against it. `tests/stackapp.ts` and `tests/helpers.ts` still target
the earlier bonding-curve/tax design (`buy`, `sell`, `Position`,
`Reputation`, ...) and do not compile against the current program's IDL —
rewriting them for the current design is a known, separate piece of work,
not yet done.

To deploy to devnet:

```bash
solana config set --url devnet
solana airdrop 5
anchor deploy --provider.cluster devnet
```

### 3. The indexer and the UI — **one Python process**

```bash
cd indexer
python -m unittest discover -s tests -t .     # stdlib only, no install
pip install fastapi "uvicorn[standard]" websockets httpx jinja2
python -m stackapp_indexer --mock             # http://127.0.0.1:8787
```

That serves everything: the pages (`/`, `/token/<mint>`, `/feed`), the JSON
API under `/api`, and the live WebSocket feed. `--mock` drives it from the
simulator, so it works with no deployed program, no airdrop and no network.
Drop `--mock` to index real devnet.

**No Node is involved.** The pages are server-rendered Jinja templates with
hand-written CSS, and transactions are assembled as *unsigned* messages by
`txbuild.py` and handed to the browser wallet to sign:

```
browser  POST /api/tx/<action> {wallet, ...}
server   builds an UNSIGNED legacy transaction message in Python
browser  window.solana.request({method:"signAndSendTransaction", ...})
```

`<action>` is one of `register_mint`, `write_registration`, `sync`, `claim`,
`donate`, `update_authority`, `initialize_config` — these are the ones the
`/api/tx/{action}` route actually dispatches. `txbuild.BUILDERS` also has a
working `reconcile` builder, but the route has no dispatch case for it, so
it's currently unreachable from the UI (callable directly against the
program instead). `pull_pump_fee` has no Python builder at all — it's a CPI
into pump.fun's own program, exercised directly against the deployed
program, not through this UI. The registration marker itself is a plain SOL
transfer, not a program instruction, so it isn't in this list either; it's
sent straight from the wallet.

No private key ever reaches the server, and no signature ever leaves the
wallet — including for `register_mint`/`write_registration`, which are
authority-gated on chain but built and signed the same way as everything
else here: whichever wallet the operator connects. If that isn't the real
`GlobalConfig.authority`, the transaction just fails on chain.

### 4. The Next.js frontend (optional)

`app/` holds the same pages as a Next.js + Tailwind app. It is entirely
optional — it exists because the original spec asked for it, and it needs a
Node toolchain. The Python UI above is the supported path.

```bash
cd app && cp .env.local.example .env.local && npm install && npm run dev
```

---

## Program accounts

| Account | PDA seeds | Holds |
|---|---|---|
| `GlobalConfig` | `["global_config"]` | the indexer backend's authority key — singleton |
| `TokenConfig` | `["config", mint]` | which mint is tracked, its informational creator, its `DepositVault` |
| `DepositVault` | `["deposit", mint]` | real lamports: registration markers, `donate`d fees, pulled pump.fun fees |
| `Registration` | `["registration", mint, owner]` | a wallet's tenure clock, live-balance-derived weight, pool checkpoint, pending/claimed rewards |
| `LoyaltyPool` | `["pool", mint]` | `acc_reward_per_share` (u128, 1e12), total weighted shares, collected/claimed/undistributed lamports |

### Instructions

`initialize_config` · `update_authority` · `register_mint` ·
`write_registration` · `sync` · `claim` · `donate` · `reconcile` ·
`pull_pump_fee`

Ordering inside every handler that touches a `Registration` is load-bearing
and identical:

```
touch_registration()   // flush buffered fee, then settle at the OLD weight
refresh_weight()       // re-price weight from the LIVE balance, adjust the pool total
collect_fee()          // only then distribute newly pulled-in fee
```

Settling *before* re-weighting is what stops a bigger balance from earning
retroactively. Re-weighting *before* collecting is what stops a claimer from
collecting on their own just-pulled fee at a weight they no longer have (or
haven't yet earned).

---

## Trust model

**This is the design decision to read `SECURITY_NOTES.md` for.**

`write_registration` is authority-gated to the indexer backend's key, and
there is no on-chain proof that the marker-SOL transfer it's supposed to be
a response to ever happened. A compromised authority key can register a
wallet that never sent a marker, or front-date `registered_at` for one that
did.

What a compromised authority **cannot** do: inflate anyone's actual payout.
`weighted_shares` is set exclusively by `sync`/`claim`, which read a
holder's live SPL balance from their own real, verified ATA every time — the
indexer never supplies a balance or a weight, only a timestamp, once. It
also can't manufacture rewards: every payout traces back to real lamports
`donate` or `pull_pump_fee` actually moved into `DepositVault`, enforced by
`checked_sub`. The net effect of a compromised authority is bounded to free
tenure-clock time on one wallet — it cannot touch the accumulator math or
drain anyone's real claim. `update_authority` exists so this key can be
rotated without redeploying; production use would need it behind real key
management (HSM / multisig), not a single hot wallet.

The full write-up, plus the fee-pull design's own history (why a simpler
automatic-pull plan was tried and rejected after checking real mainnet
state) and every other authority/CPI/arithmetic finding, is in
[`SECURITY_NOTES.md`](SECURITY_NOTES.md).

### Other known limits

- **Weight is a step function refreshed on touch.** It only changes when a
  registration is touched, so an untouched registration doesn't get its
  tenure tier applied until someone calls the permissionless `sync` crank —
  deliberate, since the alternative is iterating over holders, but it means
  `total_weighted_shares` lags reality between cranks.
- **No automatic pull from pump.fun.** `pull_pump_fee` only ever moves money
  once a creator has, on their own, added `DepositVault` as a pump.fun
  `SharingConfig` shareholder — and even then, pump.fun's own fee-sharing
  pays out the whole config at once, not one shareholder at a time.
  Otherwise the pool only grows via `donate` and `reconcile`.
- **The clock is `Clock::unix_timestamp`**, which validators can skew by a
  small amount. Tenure tiers here are minutes long, so skew is not
  load-bearing.

---

## What the tests actually prove

`cargo test` and `sim/tests/` cover the same ground in both languages:

- **Accumulator:** pro-rata splits, no retroactive earning for a registration
  that syncs after a fee already landed, the ceiling-rounding fix that keeps
  the pool from ever being over-issued, dust buffered rather than burned,
  and a 400-step adversarial sequence where claimed-plus-pending can never
  exceed collected fees.
- **Tenure weighting:** monotonic, bounded tier schedule, and that sharding
  a balance across wallets held for the same duration never sums to more
  than holding it whole.
- **Vault reconciliation:** a `DepositVault`'s real lamport balance minus
  everything the program's own bookkeeping already explains (rent floor,
  unspent marker float, already-collected fees) never double-counts, never
  panics on a desynced or deficit balance, and correctly isolates a genuine
  deficit as detectable rather than indistinguishable from "nothing new
  arrived."
- **Claim eligibility:** the `MIN_CLAIM_DELAY_SLOTS` flash-loan guard blocks
  a same-slot balance increase from claiming against a fee it didn't earn.

`indexer/tests/` adds the plumbing: borsh round-trips for every event and
account layout, PDA derivation, the store's derived numbers, HTML escaping,
every page and API route, the WebSocket handshake, and
`test_txbuild.py::test_account_shape_matches_rust`, which parses each
`#[derive(Accounts)]` struct out of the Rust and checks that the Python
transaction builders pass the same accounts in the same order with the same
signer/writable flags — Solana messages are positional, so that mismatch
would otherwise be silent until it failed on chain.

`tests/register_mint.ts` (`anchor test`) proves one specific on-chain claim:
a `DepositVault` PDA that already holds real lamports *before*
`register_mint` runs survives Anchor's `init` with that balance intact, and
the vault-reconciliation math then correctly treats it as a real, sweepable
surplus rather than losing track of it.

Three bugs were found this way during development — see
[`SECURITY_NOTES.md`](SECURITY_NOTES.md) and `math/accumulator.rs`'s doc
comments for the ceiling-rounding one specifically.

---

## Out of scope

- Mainnet deployment of any component.
- Any exchange or fiat integration.
- Custody of user private keys or user tokens — the server builds unsigned
  messages only, signing happens in the user's wallet, and holders keep
  their tokens in their own ordinary ATA at all times.
- Any claim that this tokenomics design is a good idea financially. This
  demonstrates that a live-balance-and-tenure loyalty layer can be bolted
  onto an unmodified, already-trading token and enforced on chain. Whether
  it *should* exist is a separate question.

---

## License

MIT. Provided as-is, for research and demonstration on devnet.

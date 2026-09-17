# StackApp

A **devnet prototype** of a tenure-weighted memecoin launchpad: holding longer is
mechanically rewarded, and leaving early mechanically funds the people who stayed.

---

## ⚠️ Prototype status — read this first

**This is a prototype. It runs on Solana devnet with test SOL only.**

- No mainnet program deployment, and no mainnet code path. The indexer **refuses
  to start** against a mainnet RPC endpoint (`config.py`, `BLOCKED_HOSTS`).
- No real funds, no fiat on-ramp or off-ramp, no exchange integration of any kind.
- No custody of user keys. All signing happens client-side through the wallet
  adapter (Phantom / Solflare on devnet).
- **The program has not been audited.** Taking any of this to mainnet would
  require a real third-party security audit *and* a separate legal review before
  it handles a single unit of real user value. A launchpad that takes a
  percentage of every exit and redistributes it is not a neutral piece of
  infrastructure, and the regulatory treatment of that varies by jurisdiction.
  Treat the audit and the legal review as prerequisites, not as follow-ups.

There is one known design gap that a mainnet version could not ship with — see
[Custody model](#custody-model), below. It is called out there rather than buried
because it is the difference between "the anti-gaming rules hold" and "the
anti-gaming rules hold *for tokens the program still controls*".

---

## What it does

Every exit is taxed on a curve that decays with how long **those particular
tokens** were held. The tax funds a per-mint loyalty pool paid out by
tenure-weighted share. Four rules make that hard to game:

| Rule | Mechanism |
|---|---|
| Topping up cannot borrow an old position's tenure | Positions are **FIFO lots**, each stamped with its own buy time. Averaging the timestamp would let fresh capital hide behind old tenure. |
| Moving tokens to another wallet is not a cheaper exit | `transfer_position` runs the **same** taxed path as `sell`, at the same rate. The only exempt destination is the program's own pool, and that path gives the tokens away. |
| Tenure does not travel between wallets | Transferred tokens arrive in a **new lot stamped with the current time**. |
| Sharding a position across wallets never out-earns holding it whole | Pool weight is `capital × tenure_multiplier(age)` and reputation is `capital × seconds_held`. Both are **linear in capital and count no wallets**, and every division floors, so sharding is weakly *worse*. Tier thresholds apply per wallet, so sharding strictly loses tier. |

Plus two smaller ones that matter more than they look:

- **Tax rounds up.** With floor rounding, an exit split into dust sells would pay
  zero tax. (`math/tax_curve.rs::tax_for_amount`)
- **Pool claims have a minimum block delay.** A flash-loaned position cannot buy,
  watch a victim's taxed sell inflate `acc_reward_per_share`, and claim against it
  in the same block. (`MIN_CLAIM_DELAY_SLOTS`)

The loyalty pool uses the standard O(1) reward-per-share accumulator
(MasterChef / Synthetix `rewardPerTokenStored`). Distributing tax to every holder
is a single addition. **The program never iterates over holders.**

---

## Layout

```
programs/stackapp/     Anchor program (Rust) + host-side unit tests
  src/math/            pure math: accumulator, tax curve, vesting, tenure, curve
  src/logic.rs         position/pool state transitions + anti-gaming tests
  src/instructions/    one file per instruction
sim/                   Python mirror of the same math, runnable with no toolchain
indexer/               Python indexer + the web UI
  stackapp_indexer/web/  server-rendered pages, no Node anywhere
  stackapp_indexer/txbuild.py  builds unsigned Solana transactions in Python
app/                   optional Next.js + Tailwind frontend (same pages, needs Node)
tests/                 Anchor integration tests (local validator)
```

---

## Running it

### 1. The math and the anti-gaming rules — **no toolchain needed**

`sim/` is a faithful Python mirror of the Rust. Zero dependencies, pure stdlib:

```bash
cd sim && python -m unittest discover -s tests -t .
```

A parity test parses `programs/stackapp/src/constants.rs` and fails if the two
implementations drift apart, so the mirror cannot quietly stop meaning anything.

For a narrated walk-through of each rule with real numbers:

```bash
cd sim && python scenario.py
```

### 2. The Anchor program

Needs Rust, the Solana CLI and Anchor 0.30.1. On Windows that means WSL.

```bash
cargo test --manifest-path programs/stackapp/Cargo.toml
```

runs the host-side unit tests — the accumulator, the curve, and every
anti-gaming rule — with no validator and no BPF build.

```bash
anchor build && anchor keys sync && anchor test
```

builds the program and runs `tests/stackapp.ts` against a local validator,
proving the deployed program wires that logic up correctly: right accounts,
right PDAs, right ordering, real lamports moving, real events emitted.

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

That serves everything: the pages (`/`, `/launch`, `/token/<mint>`,
`/passport/<wallet>`, `/feed`), the JSON API under `/api`, and the live
WebSocket feed. `--mock` drives it from the simulator, so it works with no
deployed program, no airdrop and no network. Drop `--mock` to index real devnet.

**No Node is involved.** The pages are server-rendered Jinja templates with
hand-written CSS, and transactions are assembled as *unsigned* messages by
`txbuild.py` and handed to the browser wallet to sign:

```
browser  POST /api/tx/buy {wallet, mint, amount}
server   builds an UNSIGNED legacy transaction message in Python
browser  window.solana.request({method:"signAndSendTransaction", ...})
```

No private key ever reaches the server, and no signature ever leaves the wallet.

### 4. The Next.js frontend (optional)

`app/` holds the same four pages as a Next.js + Tailwind app. It is entirely
optional — it exists because the original spec asked for it, and it needs a Node
toolchain. The Python UI above is the supported path.

```bash
cd app && cp .env.local.example .env.local && npm install && npm run dev
```

---

## Program accounts

| Account | PDA seeds | Holds |
|---|---|---|
| `TokenConfig` | `["config", mint]` | tax curve, vest duration, bonding-curve reserves, pool address |
| `Position` | `["position", mint, owner]` | FIFO lots, spendable balance, pool checkpoint, cost basis |
| `LoyaltyPool` | `["pool", mint]` | `acc_reward_per_share` (u128, 1e12), total weighted shares, undistributed buffer |
| `Reputation` | `["reputation", owner]` | platform-wide score, tier, tokens held to maturity |
| `CurveVault` | `["curve_vault", mint]` | lamports backing the bonding curve |

`Reputation` is non-transferable by construction: the PDA is derived from
`owner`, the struct has no authority field, and no instruction reassigns or
closes it.

### Instructions

`initialize_launch` · `buy` · `claim_vested` · `sell` · `transfer_position` ·
`donate_to_pool` · `claim_pool_share` · `update_reputation` · `sync_weight` ·
`compact_lots`

Ordering inside every handler is load-bearing and identical:

```
touch_position()   // flush buffered tax, then settle at the OLD weight
...mutate lots...
refresh_weight()   // re-price weight, adjust the pool total
collect_tax()      // only then distribute new tax
```

Settling *before* re-weighting is what stops a buy from earning retroactively.
Re-weighting *before* collecting is what stops a seller from collecting on their
own exit tax at a weight they no longer hold.

---

## Custody model

**This is the design decision to argue with first.**

The program does not hand out freely transferable SPL tokens. A holder's balance
*is* their `Position` account, which only the program can mutate. That is what
makes "every balance decrease is taxed" true rather than aspirational: there is
no path around the program, so there is no path around the tax.

The cost is that the token is not a normal SPL token. It cannot be sent to an
exchange, held in a standard wallet balance, or used in another protocol. For a
prototype whose entire point is demonstrating the anti-gaming rules, that trade
is worth making — the rules are the product, and a version that let tokens escape
into untaxed ATAs would be demonstrating nothing.

A production version cannot ship this way. It would need **Token-2022 transfer
hooks** plus a permanent-delegate or default-frozen configuration so the same
tax logic fires on genuinely circulating tokens. That is a materially different
program with a materially larger attack surface, and it is the main reason the
audit above is a prerequisite rather than a formality.

### Other known limits

- **Tenure weight is a step function refreshed on touch.** Weight only changes
  when a position is touched, so a position that sits untouched does not get its
  tier bump until someone calls the permissionless `sync_weight` crank. This is
  deliberate — the alternative is iterating over holders — but it means
  `total_weighted_shares` lags reality between cranks.
- **`MAX_LOTS` is 24.** A position at the cap must call `compact_lots`, which
  merges the two oldest lots and takes the **newer** timestamp. Compaction is
  therefore always a cost to the owner and can never launder fresh tokens into an
  old lot's rate.
- **Rounding always favours the pool.** Holders can be short-changed by up to one
  unit per claim. The pool can never become insolvent; that direction is tested.
- **The clock is `Clock::unix_timestamp`**, which validators can skew by a small
  amount. Every curve boundary here is minutes or longer, so skew is not
  load-bearing — but a curve with second-level boundaries would be a bad idea.

---

## What the tests actually prove

`cargo test` and `sim/tests/` cover the same ground in both languages:

- **Accumulator:** pro-rata splits, no retroactive earning for late joiners,
  double-claim prevention, dust buffered rather than burned, and an adversarial
  400-step sequence where payouts can never exceed deposits.
- **Tax curve:** monotonic validation, step lookup, ceiling rounding, and that
  splitting an exit into dust never reduces total tax.
- **FIFO:** that a top-up is charged *more* under FIFO than under an average-age
  model — the reason lots exist at all.
- **Transfers:** taxed identically to sells, tenure restarting on arrival, and
  that hopping through a fresh wallet costs strictly more than selling directly.
- **Sharding:** across wallets, for both pool weight and reputation score,
  including that the score function is superadditive rather than concave.
- **Vesting:** linearity, monotonicity, and that unvested tokens cannot be sold.
- **Bonding curve:** `k` never decreases, round trips never profit the trader,
  and a 600-step sequence where the vault can always pay out.
- **Long-run invariants:** across 400 random operations on many wallets —
  `spendable == sum(lot.released)`, pool weight equals the sum of position
  weights, and token conservation (`tokens_sold == outstanding + pool-held`).

`indexer/tests/` adds the plumbing: borsh round-trips for every event and
account layout, PDA derivation, the store's derived numbers, HTML escaping, every
page and API route, the WebSocket handshake, and — importantly —
`test_txbuild.py::test_account_shape_matches_rust`, which parses each
`#[derive(Accounts)]` struct out of the Rust and checks that the Python
transaction builders pass the same accounts in the same order with the same
signer/writable flags. Solana messages are positional, so that mismatch would
otherwise be silent until it failed on chain.

`anchor test` adds the on-chain wiring: PDAs, account ordering, real lamport
movement, `ClaimTooSoon` actually rejecting, reputation not travelling with
tokens, and curve solvency under real transactions.

Three bugs were found this way during development:

* `distribute` buffered the *floored* remainder while the accumulator had already
  committed the fractional part, over-issuing up to one unit per distribution.
  The insolvency property test caught it; the fix is to charge the accumulator
  the ceiling of what it committed.
* `from __future__ import annotations` plus function-local FastAPI imports left
  `Request` and `WebSocket` unresolvable against module globals, which turned
  every page into a 422 and silently 403'd the WebSocket handshake. Route-level
  tests now cover both.
* A malformed address in a transaction request raised an uncaught `BorshError`
  (a 500) instead of a 400.

---

## Out of scope

- Mainnet deployment of any component.
- Any exchange or fiat integration.
- Custody of user private keys — the server builds unsigned messages only, and
  signing happens in the user's wallet, always.
- Real SPL token minting (see [Custody model](#custody-model)).
- Any claim that the tokenomics here are a good idea financially. This
  demonstrates that a set of anti-gaming rules can be enforced on chain. Whether
  a tenure-weighted launchpad *should* exist is a separate question.

---

## License

MIT. Provided as-is, for research and demonstration on devnet.

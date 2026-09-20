# Security notes

Findings from building StackApp's pump.fun loyalty layer, documented even
where things check out - the point is to give a future audit a starting
point, not just a list of things to fix.

## 1. Registration is honor-system. Payout math is not.

`write_registration` is gated to a single `GlobalConfig.authority` signer -
the indexer backend's key. There is **no on-chain proof** that the
0.001337-SOL-scale marker transfer this instruction is supposed to be a
response to ever actually happened. If that key is compromised, or the
indexer has a bug, it can:

- Register a wallet that never sent a marker at all.
- Backdate (or rather, front-date) `registered_at` for a wallet that did
  register, by simply calling `write_registration` earlier than the real
  marker landed - there's nothing checking the two against each other.

What a compromised authority **cannot** do, no matter what it writes:

- Inflate anyone's actual payout. `weighted_shares` is set exclusively by
  `sync`/`claim`, which read a holder's *live* SPL token balance from a real
  `TokenAccount`, verified on chain to be that wallet's own canonical ATA
  (`ata::derive_ata`) every single time. The indexer never supplies a
  balance or a weight - it only ever supplies a timestamp, once.
- Manufacture rewards. `collect_fee` only ever adds what `pull_pump_fee`
  actually returns (see #2), and every payout comes out of `DepositVault`'s
  real lamport balance via a `checked_sub` that fails the whole transaction
  if it would go negative.

**Net effect of a compromised authority**: it can let an unpaying wallet
start accruing tenure early, or fabricate a registration outright - a real
but bounded harm (free tenure-clock time on one wallet, not a drain on
anyone else's actual claim). It cannot touch the accumulator math itself.

**Mitigation for production**: `update_authority` exists specifically so
this key can be rotated without redeploying. Before this goes anywhere near
mainnet, that key needs to live behind whatever the team's actual key
management is (HSM / multisig-gated hot key rotation), not a single hot
wallet with no recourse if it leaks.

## 2. There is no automatic pull from pump.fun - by design, after verifying the alternative doesn't work

The original plan was: get `DepositVault` listed as one of a token's up to
10 pump.fun "Creator Fee Sharing" wallets, then permissionlessly CPI into
`collect_creator_fee` with `creator = DepositVault` to pull our cut. Before
wiring that, I verified the actual account layout against several real,
live mainnet pump.fun tokens (read-only RPC queries, no wallet or
transactions involved) instead of trusting the IDL's shape alone, and found
it doesn't work the way that plan assumed:

- `collect_creator_fee`'s `creator_vault` is a plain PDA seeded
  `["creator-vault", creator]`. Confirmed live: it's a 0-byte,
  System-Program-owned lamport account, exactly as expected.
- But for a token with fee-sharing configured, pump.fun rewrites the
  bonding curve's `creator` field to point at the `SharingConfig` PDA
  itself, not at any shareholder's wallet. That means **all fees for a
  shared token accumulate in one vault keyed to the `SharingConfig`
  address** - there is no per-shareholder vault to individually pull from.
  I confirmed this empirically: deriving `creator_vault` from two real
  configured shareholders' own wallet addresses found one nonexistent
  account and one holding only rent-exempt dust, while the vault derived
  from the `SharingConfig` PDA's own address held a real, non-round
  accumulated balance on both sampled tokens.
- The only instruction in the public `pump_fees` IDL that touches that
  shared vault afterward is `update_fee_shares`/`_v2`, gated to the
  config's `authority` signer - not something an arbitrary shareholder can
  call permissionlessly.

So simply being *listed* as a shareholder doesn't give StackApp a
permissionless way to claim its cut. The alternative - having StackApp's
program itself hold the `SharingConfig` authority for a token - was
considered and explicitly rejected: it would require every participating
creator to hand StackApp control over their own pump.fun fee-sharing
config, a much bigger trust ask than anything else in this design, for a
feature (automatic pulls) that isn't required for the accumulator to work.

**What replaces it**: `donate` (`instructions/donate.rs`), a fully
permissionless instruction anyone can call to voluntarily route lamports
into a token's `LoyaltyPool`. In practice this is meant to be the creator,
manually claiming their own pump.fun fee the normal way and then calling
`donate` with some or all of it - but the instruction itself has no idea
who's calling it or where the lamports came from, and doesn't need to.
This sidesteps the fee-sharing mechanics entirely and doesn't depend on any
undocumented pump.fun internals.

**Practical effect**: `DepositVault`'s balance, and therefore the pool,
only ever grows from registration markers and `donate` calls - never
automatically from pump.fun trading fees. The accumulator, `sync`, and
`claim`'s payout math are unaffected by this and are fully real regardless
of where a given lamport came from.

## 3. `register_mint` has no off-chain trust step to worry about

Earlier this required the backend to verify, off chain, that a creator had
configured `DepositVault` as a real fee-sharing recipient before calling
`register_mint`. That's gone along with the fee-sharing plan (#2) -
`register_mint` just starts tracking a mint; nothing about it is contingent
on any pump.fun-side configuration existing.

## 4. Arithmetic

Every lamport-touching operation in `logic.rs` and the instruction handlers
is `checked_*` (with `StackError::MathOverflow`) or `saturating_*` (chosen
for monotonic/accumulator-style fields where overflow is not a realistic
concern at any real balance). `math/tenure.rs` and `math/accumulator.rs`'s
raw multiplies (`balance * multiplier_bps`, `weighted_shares * delta`) are
u64-or-smaller operands widened into `u128` before multiplying, which is
physically incapable of overflowing at any real SOL/token-supply scale - the
same pattern the original design used throughout.

## 5. Authority checks

| Instruction | Who must sign | How it's bound |
|---|---|---|
| `initialize_config` | anyone, once | `init` on a singleton PDA - only the first call can ever succeed |
| `update_authority` | current `GlobalConfig.authority` | `has_one = authority` |
| `register_mint` | `GlobalConfig.authority` | `has_one = authority` |
| `write_registration` | `GlobalConfig.authority` | `has_one = authority` |
| `sync` | nobody in particular - fully permissionless | none; `cranker` is an unchecked signer, present only to pay the transaction fee |
| `claim` | the registration's own `owner` | `has_one = owner` on `Registration`, plus the payout lands in `owner`'s own account, which the caller supplies but cannot redirect - it's read from `Registration.owner`, not from an arbitrary account the caller names |
| `donate` | nobody in particular - fully permissionless | none; `donor` only needs to be able to pay the lamports it's donating, and can never be identified or refunded afterward |

`sync`'s complete permissionlessness is deliberate, not an oversight - see
its doc comment. It can only ever move a registration's *own* weight closer
to the truth (its real, live balance); it cannot move `pending_rewards`
anywhere, cannot pay anyone, and settling always happens before re-weighting,
so it can never let a stale weight capture a fee that was collected before
the crank ran.

## 6. CPI / reentrancy

The only CPIs in the whole program are `system_program::create_account`
(`write_registration`, funding the new `Registration` PDA from
`DepositVault`) and `system_program::transfer` (`donate`, moving the
donor's own lamports in). Neither hands control to arbitrary
caller-supplied code - both target the fixed System Program. Payouts
(`claim`'s reward transfer) go through direct `try_borrow_mut_lamports`
balance mutation rather than a CPI, which is the narrower,
no-external-code-runs option, matching the original design's `sell.rs`
pattern.

## 7. Reading token accounts manually, across two token programs

`sync` and `claim` don't deserialize a holder's token account via
`anchor_spl::token::TokenAccount` - that type requires an exact 165-byte
match (`Pack::unpack`'s contract), which fails on any real Token-2022
account carrying extension data, including every ATA the current
Associated Token Account program creates (it always adds
`ImmutableOwner`). Verified against real, live mainnet pump.fun tokens: 14
of 15 sampled use Token-2022, not legacy SPL Token, so this isn't an edge
case - it's the common case.

Instead, `ata::read_token_amount` reads the `amount` field directly out of
raw account bytes at its fixed offset (64..72), which both token programs'
base account layout share byte-for-byte regardless of what extension data
Token-2022 appends afterward. `ata::derive_ata` is parameterized on
whichever token program actually owns the mint (read directly off the
mint account, not assumed), so the ATA address checked against
`holder_token_account` is always derived under the right program. The
risk this design accepts: if a *third* token program ever became common
on pump.fun, it would be silently rejected by `is_supported_token_program`
rather than silently mis-read - a hard error, not a wrong balance.

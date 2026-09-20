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

## 2. `pull_pump_fee` is not wired to a real CPI yet

`pumpfun.rs::pull_pump_fee` is a stub that always returns `0`. I researched
the real pump.fun IDL
([`pump-fun/pump-public-docs`](https://github.com/pump-fun/pump-public-docs),
`idl/pump.json` + `idl/pump_fees.json`) and confirmed:

- Program ID `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.
- `collect_creator_fee`'s `creator` account is **not a signer** - genuinely
  permissionless, which is exactly the property `claim` needs to pull fees
  in without anyone's separate signature.
- The Jan 2026 "up to 10 wallets" fee-sharing feature is configured entirely
  through pump.fun's own UI, via `pump_fees` program instructions
  (`create_fee_sharing_config`, `update_fee_shares`/`_v2`) that the token's
  *creator* calls - StackApp is never a party to that call. It only needs
  `DepositVault`'s address to already be one of the up to 10 configured
  `Shareholder` entries by the time `register_mint` is called.

What I could **not** confirm closely enough to trust blind: whether a
configured shareholder's cut is pre-split into its own claimable vault (so
`collect_creator_fee` with `creator = DepositVault` just works unmodified),
or whether an additional distribution step has to run first. Wiring the CPI
on an unverified guess here is worse than leaving it stubbed - a wrong
account in a real CPI can fail loudly (fine) or silently move funds
somewhere unintended (not fine).

**Before this is turned on**: register a real (probably devnet) pump.fun
token, actually configure `DepositVault` as one of its fee-sharing wallets
through pump.fun's own UI, and confirm `collect_creator_fee` behaves exactly
as documented before flipping `pull_pump_fee` from a stub to a real
`invoke_signed` CPI.

**Current practical effect**: `DepositVault`'s balance only grows from
registration markers and manual/test deposits, never from real pump.fun
trading fees, until this is wired. Everything downstream - the accumulator,
`sync`, `claim`'s payout math - is fully real and tested regardless; only
the pull-in step is inert.

## 3. `register_mint`'s off-chain trust step

The backend is trusted to have actually verified, through pump.fun's own
UI/API, that a token's creator configured `DepositVault`'s address as a real
fee-sharing recipient before calling `register_mint`. The program has no way
to check this itself - there's no on-chain link between a pump.fun mint and
its `sharing_config` that this program reads or validates. If the backend
registers a mint that was never actually configured this way, `claim` will
just always find zero available fee for it (once #2 is wired) - a silent
no-op, not a fund-safety issue, but worth knowing before treating "this mint
shows up in StackApp" as proof it's actually earning anything.

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

`sync`'s complete permissionlessness is deliberate, not an oversight - see
its doc comment. It can only ever move a registration's *own* weight closer
to the truth (its real, live balance); it cannot move `pending_rewards`
anywhere, cannot pay anyone, and settling always happens before re-weighting,
so it can never let a stale weight capture a fee that was collected before
the crank ran.

## 6. CPI / reentrancy

Once #2 is wired, the only CPI in the whole program will be
`collect_creator_fee` (pump.fun) plus the `system_program::create_account`
CPI `write_registration` already uses today. Neither hands control to
arbitrary caller-supplied code - both target fixed, well-known program IDs.
Payouts (`claim`'s reward transfer, and eventually `pull_pump_fee`'s pull)
go through direct `try_borrow_mut_lamports` balance mutation rather than a
CPI wherever possible (matching the original design's `sell.rs` pattern),
which is the narrower, no-external-code-runs option when it's available.

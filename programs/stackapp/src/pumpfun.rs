//! Integration point for pump.fun's "Creator Fee Sharing" claim.
//!
//! **Not wired to a real CPI yet.** See `SECURITY_NOTES.md` for why, and
//! what verifying this against a live registered token requires before it
//! is safe to flip on.
//!
//! Researched, from the real public IDL
//! (`github.com/pump-fun/pump-public-docs`, `idl/pump.json` +
//! `idl/pump_fees.json`) rather than guessed:
//!
//! - Pump program ID: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.
//! - `collect_creator_fee` accounts, in order: `creator` (mut, **not a
//!   signer**), `creator_vault` (mut, PDA seeded on `creator`), `system_program`,
//!   `event_authority`, `program`. Confirmed non-signer is exactly what
//!   makes this callable by anyone - our `DepositVault` can be the
//!   `creator` account with no signature required from it at all.
//! - `collect_creator_fee_v2` exists for tokens that have migrated to the
//!   PumpSwap AMM and trade against a quote mint other than native SOL -
//!   out of scope for now; most tracked tokens will still be on the
//!   bonding curve given how short pump.fun lifespans usually are.
//! - The Jan 2026 "up to 10 wallets" fee-sharing feature is configured via
//!   `pump_fees` program instructions (`create_fee_sharing_config`,
//!   `update_fee_shares`/`_v2`) that the token's *creator* calls themselves
//!   through pump.fun's own UI - StackApp is never a party to that call,
//!   it only needs `DepositVault`'s address to have been added as one of
//!   the up to 10 `Shareholder { address, share_bps }` entries. What was
//!   **not** confirmed closely enough to trust blind: whether a configured
//!   shareholder's cut lands pre-split in its own vault (so
//!   `collect_creator_fee` with `creator = DepositVault` just works
//!   unmodified) or requires an additional distribution step first. That's
//!   the one thing worth testing against a real registered token before
//!   this function does anything beyond returning `Ok(0)`.

use anchor_lang::prelude::*;

/// Pull whatever pump.fun has made available to `deposit_vault` since the
/// last pull. Returns the number of new lamports that landed.
///
/// Currently a no-op: real pump.fun fees never arrive here yet. `claim`'s
/// accumulator step still runs for real off of `DepositVault`'s actual
/// lamport balance (which does grow from registration markers and any
/// manual/test deposits), so everything downstream of "some lamports showed
/// up" is fully real today - only this pull-in step is stubbed.
pub fn pull_pump_fee(_deposit_vault: &AccountInfo, _mint: &Pubkey) -> Result<u64> {
    // TODO(pumpfun-cpi): CPI into collect_creator_fee (or _v2, once a token
    // has migrated), signed via invoke_signed with DepositVault's own seeds
    // (`[SEED_DEPOSIT_VAULT, mint.as_ref(), &[bump]]`), once the
    // shareholder-payout mechanics above are verified against a live
    // registered token on devnet or mainnet.
    Ok(0)
}

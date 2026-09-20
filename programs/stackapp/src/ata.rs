//! Manual Associated Token Account verification.
//!
//! Deliberately not a dependency on `spl-associated-token-account` (see the
//! comment on `anchor-spl` in `Cargo.toml` for why) - the ATA program ID and
//! its derivation seeds are public, permanent constants, so verifying a
//! holder's token account is genuinely their canonical ATA for a mint needs
//! nothing beyond `find_program_address`.

use anchor_lang::prelude::*;

/// The Associated Token Account program. Fixed, well-known, never changes.
pub const ASSOCIATED_TOKEN_PROGRAM_ID: Pubkey =
    pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

/// Derive `owner`'s canonical ATA for `mint`, under the legacy SPL Token
/// program (not Token-2022 - out of scope for now, see `pumpfun.rs`).
pub fn derive_ata(owner: &Pubkey, mint: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[owner.as_ref(), anchor_spl::token::ID.as_ref(), mint.as_ref()],
        &ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    .0
}

//! Manual Associated Token Account verification and balance reads.
//!
//! Deliberately not a dependency on `spl-associated-token-account` or
//! `anchor-spl`'s `token`/`token_2022` features (see the removed `anchor-spl`
//! dependency note in `Cargo.toml`'s history) - the ATA program ID, both
//! token programs' IDs, and the base token-account layout are public,
//! permanent constants, so verifying a holder's token account and reading
//! its balance needs nothing beyond `find_program_address` and a manual
//! slice read.
//!
//! Real, live pump.fun mints split roughly 14:1 Token-2022 vs legacy SPL
//! Token (verified against sampled mainnet tokens) - both are handled here,
//! selected per-mint by reading the mint account's owning program on chain
//! rather than assuming one or the other.

use anchor_lang::prelude::*;

use crate::errors::StackError;

/// The Associated Token Account program. Fixed, well-known, never changes.
pub const ASSOCIATED_TOKEN_PROGRAM_ID: Pubkey =
    pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

/// Legacy SPL Token program.
pub const TOKEN_PROGRAM_ID: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");

/// Token-2022 program. The majority of real pump.fun mints use this one, not
/// the legacy program above.
pub const TOKEN_2022_PROGRAM_ID: Pubkey =
    pubkey!("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");

/// Whether `program_id` is a token program this code knows how to read.
pub fn is_supported_token_program(program_id: &Pubkey) -> bool {
    *program_id == TOKEN_PROGRAM_ID || *program_id == TOKEN_2022_PROGRAM_ID
}

/// Derive `owner`'s canonical ATA for `mint`, under whichever token program
/// actually owns `mint` on chain (`token_program` - see
/// `is_supported_token_program`). ATA addresses are seeded on the token
/// program, so using the wrong one silently derives a different, wrong
/// address rather than failing loudly - the caller must pass the mint's
/// real owning program, not assume legacy Token.
pub fn derive_ata(owner: &Pubkey, mint: &Pubkey, token_program: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[owner.as_ref(), token_program.as_ref(), mint.as_ref()],
        &ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    .0
}

/// Read a token account's `amount` field directly out of raw account data,
/// without deserializing the rest of the struct.
///
/// Both legacy Token and Token-2022 base accounts share an identical
/// 165-byte layout - `mint`(32) `owner`(32) `amount`(8) `delegate`(36)
/// `state`(1) `is_native`(12) `delegated_amount`(8) `close_authority`(36) -
/// Token-2022 only ever *appends* extension TLV data after byte 165. Reading
/// just the fixed-offset fields we need is therefore correct for both
/// programs regardless of which (if any) extensions are present, and avoids
/// needing an exact-length match the way `Pack::unpack` requires (which is
/// exactly why `anchor_spl::token::TokenAccount` fails on any Token-2022
/// account carrying extensions, e.g. every real Associated Token Account
/// created by the current ATA program, which always adds `ImmutableOwner`).
pub fn read_token_amount(account_info: &AccountInfo) -> Result<u64> {
    let data = account_info.try_borrow_data()?;
    require!(data.len() >= 165, StackError::InvalidTokenAccount);
    let state = data[108];
    require!(state != 0, StackError::InvalidTokenAccount); // 0 == Uninitialized
    let mut amount = [0u8; 8];
    amount.copy_from_slice(&data[64..72]);
    Ok(u64::from_le_bytes(amount))
}

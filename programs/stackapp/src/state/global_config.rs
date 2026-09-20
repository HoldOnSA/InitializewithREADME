use anchor_lang::prelude::*;

/// Platform-wide singleton. PDA: `["global_config"]`.
///
/// Holds the indexer backend's signing key: the only account trusted to call
/// `register_mint` and `write_registration`. See `SECURITY_NOTES.md` for
/// exactly what that trust does and does not cover.
#[account]
#[derive(InitSpace, Debug)]
pub struct GlobalConfig {
    pub authority: Pubkey,
    pub bump: u8,
}

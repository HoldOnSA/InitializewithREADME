use anchor_lang::prelude::*;

/// Platform-wide, per-wallet reputation. PDA: `["reputation", owner]`.
///
/// Non-transferable by construction: the PDA is derived from `owner`, the
/// struct has no authority field, and the program exposes no instruction that
/// reassigns or closes it.
#[account]
#[derive(InitSpace, Debug)]
pub struct Reputation {
    pub owner: Pubkey,
    /// Milli-SOL-days of capital held to maturity. Linear in capital, which is
    /// what makes wallet-splitting score-neutral rather than profitable.
    pub score: u128,
    pub tier: u8,
    pub tokens_held_to_maturity: u32,
    pub total_tenure_weighted_volume: u128,
    pub first_seen_timestamp: i64,
    pub last_update_timestamp: i64,
    pub bump: u8,
}

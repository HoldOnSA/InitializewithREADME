use anchor_lang::prelude::*;

/// A single (wallet, mint) registration and its loyalty-pool bookkeeping.
/// PDA: `["registration", mint, owner]`.
///
/// `registered_at` is honor-system: it's written once, by the indexer
/// authority, on seeing a marker-SOL transfer - there is no on-chain proof
/// that transfer happened. What is *not* honor-system is everything below
/// it: `weighted_shares` is only ever set by `sync`/`claim` reading this
/// wallet's live SPL token balance, so the reward math is fully enforced on
/// chain no matter what the indexer does or doesn't write. See
/// `SECURITY_NOTES.md`.
#[account]
#[derive(InitSpace, Debug)]
pub struct Registration {
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub registered_at: i64,
    /// `live_balance * tenure_multiplier_bps(now - registered_at) / BPS_DENOMINATOR`,
    /// refreshed every time this registration is touched.
    pub weighted_shares: u64,
    pub reward_checkpoint: u128,
    /// Lamports, not tokens - the pool is funded by real SOL.
    pub pending_rewards: u64,
    pub lifetime_rewards_claimed: u64,
    /// Flash-loan guard, mirrors the old design's `last_increase_slot`: set
    /// whenever `weighted_shares` might have gone up.
    pub last_sync_slot: u64,
    pub bump: u8,
}

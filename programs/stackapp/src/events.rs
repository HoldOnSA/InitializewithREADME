//! Program events.
//!
//! The indexer consumes these from `Program data:` log lines, so every state
//! change worth showing the UI emits one.

use anchor_lang::prelude::*;

#[event]
pub struct MintRegistered {
    pub mint: Pubkey,
    pub creator: Pubkey,
    pub deposit_vault: Pubkey,
    pub registered_at: i64,
}

/// Emitted when the indexer writes a `Registration` after observing a
/// holder's marker-SOL transfer.
#[event]
pub struct WalletRegistered {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub registered_at: i64,
}

#[event]
pub struct WeightSynced {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub balance: u64,
    pub previous_weight: u64,
    pub new_weight: u64,
    pub total_weighted_shares: u64,
    pub timestamp: i64,
}

/// Emitted whenever `donate` routes newly available lamports into the
/// accumulator - the only source of real fee revenue.
#[event]
pub struct FeeCollected {
    pub mint: Pubkey,
    pub amount: u64,
    pub acc_reward_per_share: u128,
    pub total_weighted_shares: u64,
    pub total_collected: u64,
    pub undistributed: u64,
    pub timestamp: i64,
}

#[event]
pub struct RewardClaimed {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub amount: u64,
    pub weighted_shares: u64,
    pub lifetime_claimed: u64,
    pub timestamp: i64,
}

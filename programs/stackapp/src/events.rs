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

/// Emitted by `reconcile` when the vault's real balance comes in *under*
/// what its own bookkeeping (rent floor + unspent marker float + pool-backed
/// balance) says it should hold - should never happen under correct
/// operation. `reconcile` still fails its `require!` after this (there is
/// nothing to sweep either way), so this only ever reaches an indexer that
/// doesn't discard logs from a failed transaction - see
/// `subscriber.py::_handle_logs`.
#[event]
pub struct VaultDeficitDetected {
    pub mint: Pubkey,
    pub deposit_vault: Pubkey,
    pub actual_lamports: u64,
    pub expected_lamports: u64,
    pub deficit: u64,
    pub timestamp: i64,
}

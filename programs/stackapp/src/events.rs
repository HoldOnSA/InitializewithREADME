//! Program events.
//!
//! The indexer consumes these from `Program data:` log lines over a WebSocket
//! `logsSubscribe`, so every state change the UI cares about emits one.

use anchor_lang::prelude::*;

#[event]
pub struct LaunchInitialized {
    pub mint: Pubkey,
    pub creator: Pubkey,
    pub launch_timestamp: i64,
    pub vest_duration_seconds: i64,
    pub virtual_sol_reserves: u64,
    pub virtual_token_reserves: u64,
    pub tax_curve_points: u8,
    pub opening_tax_bps: u16,
    pub floor_tax_bps: u16,
}

#[event]
pub struct BuyExecuted {
    pub mint: Pubkey,
    pub buyer: Pubkey,
    pub amount: u64,
    pub cost_lamports: u64,
    pub timestamp: i64,
    pub slot: u64,
    pub position_total: u64,
    pub spot_price_lamports: u64,
    pub tokens_sold: u64,
}

#[event]
pub struct VestedClaimed {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub released: u64,
    pub spendable_total: u64,
    pub locked_total: u64,
    pub timestamp: i64,
}

/// Emitted for every balance decrease, whichever instruction caused it.
#[event]
pub struct ExitExecuted {
    pub mint: Pubkey,
    pub owner: Pubkey,
    /// "sell" | "transfer" | "donate"
    pub kind: u8,
    pub gross: u64,
    pub tax: u64,
    pub net: u64,
    pub top_tax_bps: u16,
    pub proceeds_lamports: u64,
    /// `Pubkey::default()` for a sell.
    pub destination: Pubkey,
    pub timestamp: i64,
}

#[event]
pub struct TaxCollected {
    pub mint: Pubkey,
    pub payer: Pubkey,
    pub amount: u64,
    pub acc_reward_per_share: u128,
    pub total_weighted_shares: u64,
    pub pool_total_collected: u64,
    pub undistributed: u64,
    pub timestamp: i64,
}

#[event]
pub struct PoolClaimed {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub amount: u64,
    pub weighted_shares: u64,
    pub lifetime_claimed: u64,
    pub timestamp: i64,
}

#[event]
pub struct WeightSynced {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub previous_weight: u64,
    pub new_weight: u64,
    pub total_weighted_shares: u64,
    pub timestamp: i64,
}

#[event]
pub struct ReputationUpdated {
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub score_delta: u128,
    pub score: u128,
    pub tier: u8,
    pub tokens_held_to_maturity: u32,
    pub total_tenure_weighted_volume: u128,
    pub capital_at_risk_lamports: u64,
    pub window_seconds: i64,
    pub timestamp: i64,
}

#[event]
pub struct TierUp {
    pub owner: Pubkey,
    pub previous_tier: u8,
    pub new_tier: u8,
    pub score: u128,
    pub timestamp: i64,
}

#[event]
pub struct LotsCompacted {
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub lots_remaining: u8,
    pub merged_timestamp: i64,
}

/// Exit kinds for `ExitExecuted::kind`.
pub const EXIT_KIND_SELL: u8 = 0;
pub const EXIT_KIND_TRANSFER: u8 = 1;
pub const EXIT_KIND_DONATE: u8 = 2;

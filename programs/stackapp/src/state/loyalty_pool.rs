use anchor_lang::prelude::*;

/// Per-mint loyalty pool. PDA: `["pool", mint]`.
///
/// Implements the standard O(1) reward-per-share accumulator (MasterChef /
/// Synthetix `rewardPerTokenStored`). Distributing tax to every holder is a
/// single addition to `acc_reward_per_share`; the program never iterates over
/// holders.
#[account]
#[derive(InitSpace, Debug)]
pub struct LoyaltyPool {
    pub mint: Pubkey,
    /// Cumulative reward per weighted share, scaled by `ACC_PRECISION` (1e12).
    pub acc_reward_per_share: u128,
    /// Sum of every position's `weighted_shares`.
    pub total_weighted_shares: u64,
    /// Lifetime tax routed into this pool.
    pub total_collected: u64,
    /// Lifetime rewards paid out.
    pub total_claimed: u64,
    /// Tax collected while `total_weighted_shares == 0`, plus per-distribution
    /// rounding dust. Flushed into the accumulator as soon as there is weight
    /// to receive it, so no tax is ever silently burned.
    pub undistributed: u64,
    pub bump: u8,
}

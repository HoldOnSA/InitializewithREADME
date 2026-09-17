use anchor_lang::prelude::*;

use crate::constants::*;

/// A single FIFO purchase lot.
///
/// `original` never changes. `cum_released` is the monotonic amount of this lot
/// that vesting has ever unlocked; `released` is how much of that is still
/// sitting in the owner's spendable balance. Everything else about the lot is
/// derived, which keeps the invariants checkable:
///
/// * `locked   = original - cum_released`
/// * `sold     = cum_released - released`
/// * `remaining = original - sold = locked + released`
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, Debug, Default, PartialEq, Eq, InitSpace)]
pub struct Lot {
    pub original: u64,
    pub cum_released: u64,
    pub released: u64,
    pub buy_timestamp: i64,
}

impl Lot {
    pub fn new(amount: u64, buy_timestamp: i64) -> Self {
        Self { original: amount, cum_released: 0, released: 0, buy_timestamp }
    }

    /// A lot that is immediately liquid (used for pool reward payouts, which
    /// are not subject to vesting but *are* freshly time-stamped so that
    /// dumping them straight away pays the top of the tax curve).
    pub fn liquid(amount: u64, buy_timestamp: i64) -> Self {
        Self { original: amount, cum_released: amount, released: amount, buy_timestamp }
    }

    pub fn locked(&self) -> u64 {
        self.original.saturating_sub(self.cum_released)
    }

    pub fn remaining(&self) -> u64 {
        self.locked().saturating_add(self.released)
    }

    pub fn is_empty(&self) -> bool {
        self.remaining() == 0
    }
}

/// Per (wallet, mint) ledger. PDA: `["position", mint, owner]`.
///
/// This account *is* the holder's balance. The program never hands out freely
/// transferable tokens, which is what makes "every balance decrease is taxed"
/// enforceable in this prototype (see README, "Custody model").
#[account]
#[derive(InitSpace, Debug)]
pub struct Position {
    pub owner: Pubkey,
    pub mint: Pubkey,
    #[max_len(MAX_LOTS)]
    pub lots: Vec<Lot>,

    /// Lifetime total moved from locked -> spendable by `claim_vested`.
    pub vested_claimed: u64,
    /// Currently unlocked and sellable. Invariant: `== sum(lot.released)`.
    pub spendable: u64,

    // --- loyalty pool accounting ---
    /// Tenure-weighted shares this position currently contributes to
    /// `LoyaltyPool::total_weighted_shares`.
    pub weighted_shares: u64,
    /// `LoyaltyPool::acc_reward_per_share` as of the last settle.
    pub reward_checkpoint: u128,
    /// Settled but not yet withdrawn rewards.
    pub pending_rewards: u64,
    pub lifetime_rewards_claimed: u64,

    /// Slot of the most recent balance *increase*. Gates `claim_pool_share`.
    pub last_increase_slot: u64,

    // --- reputation inputs ---
    /// Lamports paid in, reduced pro-rata on exit. This is "capital at risk".
    pub cost_basis_lamports: u64,
    pub total_bought: u64,
    pub total_sold: u64,
    pub first_buy_timestamp: i64,
    /// Accumulates `amount_sold * age_at_sale` across every exit.
    pub tenure_weighted_volume: u128,
    /// Watermark of `tenure_weighted_volume` already pushed into `Reputation`.
    pub credited_volume: u128,
    /// Timestamp of the last successful `update_reputation`.
    pub last_reputation_timestamp: i64,
    /// How many times this position has been credited with maturity.
    pub maturity_credits: u32,

    pub bump: u8,
}

impl Position {
    /// Total tokens still attributed to this position (locked + spendable).
    pub fn total_remaining(&self) -> u64 {
        self.lots.iter().fold(0u64, |acc, l| acc.saturating_add(l.remaining()))
    }

    pub fn total_locked(&self) -> u64 {
        self.lots.iter().fold(0u64, |acc, l| acc.saturating_add(l.locked()))
    }
}

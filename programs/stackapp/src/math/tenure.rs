//! Tenure weighting for a single token's holding duration.
//!
//! Weight is `live_balance x tenure_multiplier(seconds_held)` - linear in
//! balance, floored, with no per-wallet bonus of any kind. That's the whole
//! anti-sybil argument: splitting one wallet's balance across N registered
//! wallets held for the identical duration sums back to exactly the same
//! total weight (up to integer-rounding dust), so sharding buys nothing.

use crate::constants::{BPS_DENOMINATOR, TENURE_TIER_MULTIPLIER_BPS, TENURE_TIER_SECONDS};

/// Tenure multiplier in basis points for a wallet that has held for
/// `seconds_held` since registration.
pub fn tenure_multiplier_bps(seconds_held: i64) -> u64 {
    let held = if seconds_held < 0 { 0 } else { seconds_held };
    let mut mult = TENURE_TIER_MULTIPLIER_BPS[0];
    for (i, threshold) in TENURE_TIER_SECONDS.iter().enumerate() {
        if held >= *threshold {
            mult = TENURE_TIER_MULTIPLIER_BPS[i + 1];
        } else {
            break;
        }
    }
    mult
}

/// Weighted shares for a live balance held `seconds_held` since registration.
pub fn weight_for_balance(balance: u64, seconds_held: i64) -> u64 {
    let mult = tenure_multiplier_bps(seconds_held);
    (((balance as u128) * (mult as u128)) / (BPS_DENOMINATOR as u128)).min(u64::MAX as u128) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    const MIN: i64 = 60;
    const MIN10: i64 = 600;
    const MIN30: i64 = 1_800;

    #[test]
    fn tier_zero_is_1x() {
        assert_eq!(tenure_multiplier_bps(0), BPS_DENOMINATOR);
        assert_eq!(tenure_multiplier_bps(MIN - 1), BPS_DENOMINATOR);
    }

    #[test]
    fn multiplier_steps_up_with_age() {
        assert_eq!(tenure_multiplier_bps(MIN), TENURE_TIER_MULTIPLIER_BPS[1]);
        assert_eq!(tenure_multiplier_bps(MIN10), TENURE_TIER_MULTIPLIER_BPS[2]);
        assert_eq!(tenure_multiplier_bps(MIN30), TENURE_TIER_MULTIPLIER_BPS[3]);
        assert_eq!(tenure_multiplier_bps(MIN30 + 1_000_000), TENURE_TIER_MULTIPLIER_BPS[3]);
    }

    #[test]
    fn multiplier_schedule_is_bounded_and_non_decreasing() {
        // An unbounded multiplier would let the earliest holder permanently
        // capture the pool no matter how much later capital arrives.
        assert_eq!(TENURE_TIER_MULTIPLIER_BPS[0], BPS_DENOMINATOR, "tier 0 must be 1.00x");
        assert_eq!(
            TENURE_TIER_MULTIPLIER_BPS,
            {
                let mut sorted = TENURE_TIER_MULTIPLIER_BPS;
                sorted.sort();
                sorted
            },
            "tiers must not go backwards"
        );
        assert!(*TENURE_TIER_MULTIPLIER_BPS.iter().max().unwrap() <= 3 * BPS_DENOMINATOR, "cap is 3x");
    }

    #[test]
    fn negative_held_time_is_clamped_to_the_floor() {
        assert_eq!(tenure_multiplier_bps(-100), BPS_DENOMINATOR);
    }

    #[test]
    fn weight_scales_with_both_balance_and_time() {
        let base = weight_for_balance(1_000_000, 0);
        assert_eq!(base, 1_000_000); // 1.00x at tier 0

        let held_a_minute = weight_for_balance(1_000_000, MIN);
        assert!(held_a_minute > base, "holding past the first tier must weigh more");

        let double_balance = weight_for_balance(2_000_000, MIN);
        assert_eq!(double_balance, held_a_minute * 2, "weight is linear in balance");
    }

    #[test]
    fn a_zero_balance_carries_no_weight() {
        assert_eq!(weight_for_balance(0, MIN30), 0);
    }

    #[test]
    fn sharding_across_wallets_never_out_weighs_one_wallet() {
        // The core anti-sybil property: split the same balance across many
        // wallets, held for the identical duration, and the sum can only be
        // less than or equal to the whole - never more.
        let whole_balance = 1_000_003u64; // deliberately not a round multiple
        let held = MIN10;
        let whole_weight = weight_for_balance(whole_balance, held);

        for shards in [2u64, 3, 5, 7, 11] {
            let shard_balance = whole_balance / shards;
            let shard_weight = weight_for_balance(shard_balance, held);
            let summed = shard_weight.saturating_mul(shards);
            assert!(
                summed <= whole_weight,
                "{} shards of {} summed to {} > whole wallet's {}",
                shards,
                shard_balance,
                summed,
                whole_weight
            );
        }
    }
}

//! Tenure weighting and reputation scoring.
//!
//! Both functions here are deliberately **linear in capital** and floored.
//! That is the whole anti-sybil argument:
//!
//! * A concave function of capital (`sqrt`, `log`, or anything with a per-wallet
//!   bonus) pays `n * f(C/n) > f(C)`, i.e. it *pays* you to shard one position
//!   across many wallets.
//! * A linear function pays exactly `f(C)` either way, and because every step
//!   floors, sharding is weakly *worse* - you lose one unit of dust per shard,
//!   plus rent for each extra PDA.
//!
//! Nothing here counts wallets. Weight is `capital x tenure_multiplier(age)`
//! and score is `capital x seconds_held`.

use crate::constants::{
    BPS_DENOMINATOR, MAX_REPUTATION_TIER, REPUTATION_TIER_THRESHOLDS, REP_SCORE_DIVISOR,
    TENURE_TIERS,
};
use crate::state::Lot;

/// Tenure multiplier in basis points for a lot of the given age.
///
/// Bounded at 3x on purpose: an unbounded multiplier would make weight
/// super-linear in time and let the earliest wallet permanently capture the
/// pool no matter how much later capital arrives.
pub fn tenure_multiplier_bps(age_seconds: i64) -> u64 {
    let age = if age_seconds < 0 { 0 } else { age_seconds };
    let mut mult = TENURE_TIERS[0].1;
    for (min_age, m) in TENURE_TIERS.iter() {
        if *min_age <= age {
            mult = *m;
        } else {
            break;
        }
    }
    mult
}

/// Weighted shares contributed by a single lot.
pub fn lot_weight(lot: &Lot, now: i64) -> u64 {
    let remaining = lot.remaining();
    if remaining == 0 {
        return 0;
    }
    let mult = tenure_multiplier_bps(now - lot.buy_timestamp);
    (((remaining as u128) * (mult as u128)) / (BPS_DENOMINATOR as u128)).min(u64::MAX as u128) as u64
}

/// Total weighted shares for a position's lots.
///
/// Note this is a snapshot: weight only changes in storage when a position is
/// explicitly touched (buy / sell / transfer / claim / `sync_weight`), which is
/// what keeps `LoyaltyPool::total_weighted_shares` consistent without the
/// program ever iterating over holders.
pub fn weighted_shares_for_lots(lots: &[Lot], now: i64) -> u64 {
    lots.iter()
        .fold(0u64, |acc, lot| acc.saturating_add(lot_weight(lot, now)))
}

/// Reputation score earned by holding `capital_lamports` for `window_seconds`.
///
/// Unit is milli-SOL-days: 1 SOL held for 1 day == 1000 points.
pub fn reputation_score_delta(capital_lamports: u64, window_seconds: i64) -> u128 {
    if capital_lamports == 0 || window_seconds <= 0 {
        return 0;
    }
    ((capital_lamports as u128) * (window_seconds as u128)) / REP_SCORE_DIVISOR
}

/// Tier for a score. Tiers are thresholds on a *single wallet's* score, so
/// sharding a position dilutes tier even though it leaves total score flat.
pub fn tier_for_score(score: u128) -> u8 {
    let mut tier: u8 = 0;
    for threshold in REPUTATION_TIER_THRESHOLDS.iter() {
        if score >= *threshold {
            tier += 1;
        } else {
            break;
        }
    }
    tier.min(MAX_REPUTATION_TIER)
}

#[cfg(test)]
mod tests {
    use super::*;

    const DAY: i64 = 86_400;
    const SOL: u64 = 1_000_000_000;

    fn held_lot(amount: u64, age: i64) -> Lot {
        // A fully released lot of `amount`, bought `age` seconds ago.
        Lot { original: amount, cum_released: amount, released: amount, buy_timestamp: -age }
    }

    #[test]
    fn multiplier_steps_up_with_age() {
        assert_eq!(tenure_multiplier_bps(0), 10_000);
        assert_eq!(tenure_multiplier_bps(DAY - 1), 10_000);
        assert_eq!(tenure_multiplier_bps(DAY), 12_500);
        assert_eq!(tenure_multiplier_bps(7 * DAY), 15_000);
        assert_eq!(tenure_multiplier_bps(30 * DAY), 20_000);
        assert_eq!(tenure_multiplier_bps(90 * DAY), 30_000);
        assert_eq!(tenure_multiplier_bps(3_650 * DAY), 30_000, "multiplier is capped");
        assert_eq!(tenure_multiplier_bps(-500), 10_000);
    }

    #[test]
    fn weight_scales_with_both_capital_and_time() {
        let fresh = held_lot(1_000, 0);
        let aged = held_lot(1_000, 90 * DAY);
        assert_eq!(lot_weight(&fresh, 0), 1_000);
        assert_eq!(lot_weight(&aged, 0), 3_000);

        let big_fresh = held_lot(3_000, 0);
        assert_eq!(
            lot_weight(&big_fresh, 0),
            lot_weight(&aged, 0),
            "3x capital held briefly == 1x capital held to the top tier"
        );
    }

    #[test]
    fn a_sold_out_lot_carries_no_weight() {
        let mut lot = held_lot(1_000, 30 * DAY);
        lot.released = 0; // everything released was sold
        assert_eq!(lot.remaining(), 0);
        assert_eq!(lot_weight(&lot, 0), 0);
    }

    /// Sharding one position across N wallets must not increase pool weight.
    #[test]
    fn sharding_does_not_increase_pool_weight() {
        let total = 1_000_003u64;
        let age = 45 * DAY;

        let whole = lot_weight(&held_lot(total, age), 0);

        for shards in [2u64, 3, 7, 100] {
            let per = total / shards;
            let remainder = total % shards;
            let mut sum = 0u64;
            for i in 0..shards {
                let amount = per + if i < remainder { 1 } else { 0 };
                sum += lot_weight(&held_lot(amount, age), 0);
            }
            assert!(
                sum <= whole,
                "{} shards produced weight {} > {} held whole",
                shards,
                sum,
                whole
            );
        }
    }

    /// The same property for reputation score. Floors make it weakly worse to
    /// shard; it is never better.
    #[test]
    fn sharding_does_not_increase_reputation_score() {
        let capital = 37 * SOL + 12_345;
        let window = 63 * DAY;
        let whole = reputation_score_delta(capital, window);

        for shards in [2u64, 5, 13, 50] {
            let per = capital / shards;
            let remainder = capital % shards;
            let mut sum = 0u128;
            for i in 0..shards {
                let c = per + if i < remainder { 1 } else { 0 };
                sum += reputation_score_delta(c, window);
            }
            assert!(
                sum <= whole,
                "{} shards scored {} > {} held whole",
                shards,
                sum,
                whole
            );
        }
    }

    /// And sharding strictly *loses* tier, because tiers are per-wallet
    /// thresholds.
    #[test]
    fn sharding_dilutes_tier() {
        let capital = 4 * SOL;
        let window = 30 * DAY;

        // One wallet holding the lot.
        let whole_score = reputation_score_delta(capital, window);
        let whole_tier = tier_for_score(whole_score);

        // Four wallets each holding a quarter.
        let shard_score = reputation_score_delta(capital / 4, window);
        let shard_tier = tier_for_score(shard_score);

        assert!(
            shard_tier < whole_tier,
            "sharding should reduce per-wallet tier ({} -> {})",
            whole_tier,
            shard_tier
        );
        // Total score is unchanged, so this is a pure loss for the sharder.
        assert_eq!(shard_score * 4, whole_score);
    }

    #[test]
    fn score_is_superadditive_never_concave() {
        // f(a + b) >= f(a) + f(b) for every split. A concave scoring function
        // would fail this and make sharding profitable.
        let window = 11 * DAY;
        for a in [1u64, 999, SOL, 7 * SOL + 3] {
            for b in [1u64, 12_345, 3 * SOL, 91 * SOL - 7] {
                let joint = reputation_score_delta(a + b, window);
                let split = reputation_score_delta(a, window) + reputation_score_delta(b, window);
                assert!(joint >= split, "f({}+{}) = {} < {}", a, b, joint, split);
            }
        }
    }

    #[test]
    fn score_units_are_milli_sol_days() {
        assert_eq!(reputation_score_delta(SOL, DAY), 1_000);
        assert_eq!(reputation_score_delta(10 * SOL, DAY), 10_000);
        assert_eq!(reputation_score_delta(SOL, 0), 0);
        assert_eq!(reputation_score_delta(0, DAY), 0);
    }

    #[test]
    fn tiers_follow_thresholds() {
        assert_eq!(tier_for_score(0), 0);
        assert_eq!(tier_for_score(999), 0);
        assert_eq!(tier_for_score(1_000), 1);
        assert_eq!(tier_for_score(9_999), 1);
        assert_eq!(tier_for_score(10_000), 2);
        assert_eq!(tier_for_score(50_000), 3);
        assert_eq!(tier_for_score(250_000), 4);
        assert_eq!(tier_for_score(u128::MAX), MAX_REPUTATION_TIER);
    }
}

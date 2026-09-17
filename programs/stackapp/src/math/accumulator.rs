//! The O(1) reward-per-share accumulator.
//!
//! This is the MasterChef / Synthetix `rewardPerTokenStored` pattern. A
//! distribution to N holders is a single addition; a holder's entitlement is a
//! single multiplication against the delta since their checkpoint. The program
//! never iterates over holders.

use crate::constants::ACC_PRECISION;

/// Fold `amount` into the accumulator.
///
/// Returns `(new_acc_reward_per_share, undistributed_remainder)`.
///
/// The remainder covers two cases, and in both the caller must buffer it so no
/// tax is silently burned:
///
/// 1. `total_weighted_shares == 0` - nobody is eligible yet, so the whole
///    amount is buffered.
/// 2. Integer truncation - `delta * total / ACC_PRECISION` can be slightly less
///    than `amount`; that dust is buffered.
///
/// Invariant (proved in tests): the sum of what every holder can subsequently
/// claim is always `<= amount`. Rounding always favours the pool, never the
/// holder, so the pool can never become insolvent.
pub fn distribute(acc: u128, total_weighted_shares: u64, amount: u64) -> (u128, u64) {
    if amount == 0 {
        return (acc, 0);
    }
    if total_weighted_shares == 0 {
        return (acc, amount);
    }

    let total = total_weighted_shares as u128;
    let scaled = match (amount as u128).checked_mul(ACC_PRECISION) {
        Some(v) => v,
        None => return (acc, amount),
    };
    let delta = scaled / total;
    if delta == 0 {
        // Too small to move the accumulator at this precision. Buffer it; it
        // will be flushed once enough dust has piled up behind it.
        return (acc, amount);
    }

    // Charge the accumulator the CEILING of what it just committed, not the
    // floor. The exact entitlement `delta * total / ACC_PRECISION` is usually
    // fractional; buffering `amount - floor(..)` would leave that fraction
    // claimable in the accumulator *and* re-buffer it for redistribution,
    // over-issuing up to one unit per distribution. Rounding the charge up
    // cannot exceed `amount`, because `delta` was itself floored.
    let committed = crate::math::ceil_div(delta * total, ACC_PRECISION).min(amount as u128) as u64;
    let leftover = amount - committed;

    match acc.checked_add(delta) {
        Some(new_acc) => (new_acc, leftover),
        None => (acc, amount),
    }
}

/// Rewards owed to a holder since their last checkpoint.
///
/// `pending = weighted_shares * (acc_now - checkpoint) / ACC_PRECISION`
pub fn pending(weighted_shares: u64, acc_now: u128, checkpoint: u128) -> u64 {
    if weighted_shares == 0 || acc_now <= checkpoint {
        return 0;
    }
    let delta = acc_now - checkpoint;
    let product = match (weighted_shares as u128).checked_mul(delta) {
        Some(v) => v,
        None => return u64::MAX,
    };
    (product / ACC_PRECISION).min(u64::MAX as u128) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distribute_with_no_shares_buffers_everything() {
        let (acc, leftover) = distribute(0, 0, 5_000);
        assert_eq!(acc, 0);
        assert_eq!(leftover, 5_000, "tax must never be burned when nobody is eligible");
    }

    #[test]
    fn distribute_splits_pro_rata() {
        // Two holders with 1_000 and 3_000 weighted shares. Distribute 4_000.
        let (acc, leftover) = distribute(0, 4_000, 4_000);
        assert_eq!(leftover, 0);
        assert_eq!(pending(1_000, acc, 0), 1_000);
        assert_eq!(pending(3_000, acc, 0), 3_000);
    }

    #[test]
    fn checkpointing_prevents_double_claim() {
        let (acc1, _) = distribute(0, 1_000, 1_000);
        assert_eq!(pending(1_000, acc1, 0), 1_000);
        // Holder settles at acc1, then a second distribution lands.
        let (acc2, _) = distribute(acc1, 1_000, 500);
        assert_eq!(pending(1_000, acc2, acc1), 500);
        assert_eq!(pending(1_000, acc1, acc1), 0, "a settled holder is owed nothing");
    }

    #[test]
    fn late_joiner_earns_nothing_retroactively() {
        // Alice alone with 1_000 shares; 1_000 is distributed to her.
        let (acc1, _) = distribute(0, 1_000, 1_000);

        // Bob joins now and checkpoints at the *current* accumulator. Even with
        // 9_000x Alice's stake he is owed nothing for the past distribution.
        let bob_checkpoint = acc1;
        assert_eq!(pending(9_000_000, acc1, bob_checkpoint), 0);

        // The next distribution splits strictly pro-rata going forward.
        let total = 1_000 + 9_000_000;
        let (acc2, _) = distribute(acc1, total, total);
        assert_eq!(pending(1_000, acc2, acc1), 1_000);
        assert_eq!(pending(9_000_000, acc2, bob_checkpoint), 9_000_000);
    }

    /// The solvency invariant: across an adversarial sequence of joins, exits
    /// and distributions, holders can never claim more than was distributed.
    #[test]
    fn pool_is_never_insolvent() {
        struct Holder {
            shares: u64,
            checkpoint: u128,
        }

        let mut acc: u128 = 0;
        let mut buffered: u64 = 0;
        let mut deposited: u64 = 0;
        let mut total_claimed: u64 = 0;

        let mut holders: Vec<Holder> = Vec::new();
        let mut total_shares: u64 = 0;

        // Deterministic but lumpy pseudo-random schedule.
        let mut seed: u64 = 0x5EED_1234;
        let mut next = move || {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            seed >> 33
        };

        for step in 0..400u64 {
            match step % 4 {
                0 => {
                    // A holder joins with an awkward (prime-ish) share count.
                    let shares = 1 + next() % 7_919;
                    holders.push(Holder { shares, checkpoint: acc });
                    total_shares += shares;
                }
                1 | 2 => {
                    // Tax arrives. Anything previously buffered rides along.
                    let amount = 1 + next() % 1_000_003;
                    deposited += amount;
                    let feed = amount + buffered;
                    let (new_acc, leftover) = distribute(acc, total_shares, feed);
                    acc = new_acc;
                    buffered = leftover;
                }
                _ => {
                    // A holder settles and exits.
                    if !holders.is_empty() {
                        let idx = (next() as usize) % holders.len();
                        total_claimed += pending(holders[idx].shares, acc, holders[idx].checkpoint);
                        total_shares -= holders[idx].shares;
                        holders.remove(idx);
                    }
                }
            }
        }

        // Settle everyone still in the pool.
        for h in holders.iter() {
            total_claimed += pending(h.shares, acc, h.checkpoint);
        }

        assert!(
            total_claimed <= deposited,
            "claimed {} exceeded deposited {}",
            total_claimed,
            deposited
        );
        // And the accounting should be tight, not merely safe: at most one unit
        // of dust per distribution event should be left stranded.
        let stranded = deposited - total_claimed;
        assert!(
            stranded <= buffered + 400,
            "stranded {} is larger than buffered dust {} plus slack",
            stranded,
            buffered
        );
    }

    #[test]
    fn dust_is_buffered_not_burned() {
        // One unit of tax against a share count larger than ACC_PRECISION
        // cannot move the accumulator, so it must be buffered, not lost.
        let huge = 10_000_000_000_000u64; // 1e13 > ACC_PRECISION (1e12)
        let (acc, leftover) = distribute(0, huge, 1);
        assert_eq!(acc, 0);
        assert_eq!(leftover, 1);

        // Ten units does move it (1e12 * 10 / 1e13 == 1).
        let (acc2, leftover2) = distribute(0, huge, 10);
        assert_eq!(acc2, 1);
        assert_eq!(leftover2, 0);
    }

    #[test]
    fn distributing_zero_is_a_noop() {
        let (acc, leftover) = distribute(12_345, 1_000, 0);
        assert_eq!(acc, 12_345);
        assert_eq!(leftover, 0);
    }
}

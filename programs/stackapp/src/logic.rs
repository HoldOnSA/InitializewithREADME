//! Registration / pool state transitions.
//!
//! These operate on plain `&mut` state structs rather than on Anchor
//! accounts, so the instruction handlers stay thin and every rule here is
//! covered by host-side `cargo test` without a validator.
//!
//! Ordering matters and is the same in every handler that touches a
//! `Registration`:
//!
//! ```text
//! touch_registration()   // flush buffered fee, then settle at the OLD weight
//! refresh_weight()       // re-price weight from the LIVE balance, adjust the pool total
//! collect_fee()          // only then distribute newly pulled-in fee
//! ```
//!
//! Settling before re-weighting is what stops a bigger balance from earning
//! retroactively; re-weighting before collecting is what stops a claimer
//! from collecting on their own just-pulled fee at a weight they no longer
//! have (or haven't yet earned).

use crate::constants::*;
use crate::math::*;
use crate::state::{LoyaltyPool, Registration};

// ---------------------------------------------------------------------------
// Pool accounting
// ---------------------------------------------------------------------------

/// Push any buffered fee into the accumulator, if there is weight to receive it.
pub fn flush_undistributed(pool: &mut LoyaltyPool) {
    if pool.undistributed == 0 || pool.total_weighted_shares == 0 {
        return;
    }
    let (acc, leftover) = distribute(
        pool.acc_reward_per_share,
        pool.total_weighted_shares,
        pool.undistributed,
    );
    pool.acc_reward_per_share = acc;
    pool.undistributed = leftover;
}

/// Accrue a registration's rewards up to the pool's current accumulator.
pub fn settle_registration(registration: &mut Registration, pool: &LoyaltyPool) {
    let owed = pending(
        registration.weighted_shares,
        pool.acc_reward_per_share,
        registration.reward_checkpoint,
    );
    registration.pending_rewards = registration.pending_rewards.saturating_add(owed);
    registration.reward_checkpoint = pool.acc_reward_per_share;
}

/// Flush the pool, then settle the registration. Always the first thing a
/// handler does after loading accounts.
pub fn touch_registration(registration: &mut Registration, pool: &mut LoyaltyPool) {
    flush_undistributed(pool);
    settle_registration(registration, pool);
}

/// Re-price a registration's weight from a *live* balance and reconcile the
/// pool total. `now` and `registration.registered_at` determine the tenure
/// multiplier; `live_balance` must come from an actual on-chain SPL token
/// account read by the caller, never a cached/indexer-reported number.
pub fn refresh_weight(registration: &mut Registration, pool: &mut LoyaltyPool, live_balance: u64, now: i64) {
    let new_weight = weight_for_balance(live_balance, now - registration.registered_at);
    pool.total_weighted_shares = pool
        .total_weighted_shares
        .saturating_sub(registration.weighted_shares)
        .saturating_add(new_weight);
    registration.weighted_shares = new_weight;
}

/// Route `amount` of newly pulled-in fee into the pool.
///
/// Always buffers first and then flushes, so a fee that lands while
/// `total_weighted_shares == 0` is held rather than burned.
pub fn collect_fee(pool: &mut LoyaltyPool, amount: u64) {
    if amount == 0 {
        return;
    }
    pool.total_collected = pool.total_collected.saturating_add(amount);
    pool.undistributed = pool.undistributed.saturating_add(amount);
    flush_undistributed(pool);
}

/// Whether `registration` has waited out the anti-flash-loan delay.
pub fn claim_is_eligible(registration: &Registration, current_slot: u64) -> bool {
    current_slot >= registration.last_sync_slot.saturating_add(MIN_CLAIM_DELAY_SLOTS)
}

// ---------------------------------------------------------------------------
// Vault reconciliation
// ---------------------------------------------------------------------------

/// How much of a `DepositVault`'s real lamport balance is unaccounted for by
/// anything the program already tracks - i.e. arrived as a plain SOL
/// transfer that went through neither `donate` nor the registration-marker
/// flow. Returns 0 if nothing is unaccounted for.
///
/// Three things explain a legitimate balance without it being fee revenue:
///
/// - `own_rent_floor`: the vault account's own rent-exemption. Never
///   spendable, never anyone's reward.
/// - `total_marker_deposits - total_rent_spent`: registration-marker money
///   received but not yet consumed by the rent `write_registration` pays out
///   of the vault to create each `Registration` PDA. `REGISTRATION_MARKER_LAMPORTS`
///   is deliberately more than one PDA's rent (see its doc comment), so this
///   is normally positive and grows by a fixed amount per registration - the
///   non-refundable "registration cost" float, not a reward.
/// - `total_collected - total_claimed`: real lamports `donate` has already
///   moved into the vault and folded into the accumulator, minus whatever
///   `claim` has already paid back out of it. Already fee revenue, already
///   counted - not to be swept a second time.
///
/// Anything above the sum of those three is real, undeclared money sitting
/// in the vault, and `reconcile` folds it into the pool the same way
/// `donate` would. Every subtraction saturates rather than erroring: this
/// function has no side effects and must never be able to brick a
/// permissionless crank over an arithmetic edge case - a live vault should
/// never actually reach one, but if it did, saturating to a smaller (or
/// zero) surplus is the safe direction to be wrong in.
pub fn vault_surplus(
    actual_lamports: u64,
    own_rent_floor: u64,
    total_marker_deposits: u64,
    total_rent_spent: u64,
    total_collected: u64,
    total_claimed: u64,
) -> u64 {
    let retained_marker_float = total_marker_deposits.saturating_sub(total_rent_spent);
    let backed_by_pool = total_collected.saturating_sub(total_claimed);
    let expected = own_rent_floor
        .saturating_add(retained_marker_float)
        .saturating_add(backed_by_pool);
    actual_lamports.saturating_sub(expected)
}

#[cfg(test)]
mod tests {
    use super::*;
    use anchor_lang::prelude::Pubkey;

    fn new_registration() -> Registration {
        Registration {
            owner: Pubkey::default(),
            mint: Pubkey::default(),
            registered_at: 0,
            weighted_shares: 0,
            reward_checkpoint: 0,
            pending_rewards: 0,
            lifetime_rewards_claimed: 0,
            last_sync_slot: 0,
            bump: 0,
        }
    }

    fn new_pool() -> LoyaltyPool {
        LoyaltyPool {
            mint: Pubkey::default(),
            acc_reward_per_share: 0,
            total_weighted_shares: 0,
            total_collected: 0,
            total_claimed: 0,
            undistributed: 0,
            bump: 0,
        }
    }

    /// Simulate `sync`/`claim`'s weight-refresh step.
    fn do_sync(registration: &mut Registration, pool: &mut LoyaltyPool, balance: u64, now: i64, slot: u64) {
        touch_registration(registration, pool);
        refresh_weight(registration, pool, balance, now);
        registration.last_sync_slot = slot;
    }

    #[test]
    fn a_fresh_registration_earns_nothing_until_synced() {
        let mut pool = new_pool();
        let mut reg = new_registration();
        collect_fee(&mut pool, 1_000);
        touch_registration(&mut reg, &mut pool);
        assert_eq!(reg.pending_rewards, 0, "no weight yet - nothing to settle");
    }

    #[test]
    fn fee_buffers_until_someone_has_weight() {
        let mut pool = new_pool();
        collect_fee(&mut pool, 1_000);
        assert_eq!(pool.undistributed, 1_000);

        let mut reg = new_registration();
        do_sync(&mut reg, &mut pool, 500_000, 0, 0);
        assert_eq!(pool.total_weighted_shares, 500_000, "1.00x at tier 0");

        // Buffered fee is only flushed on the NEXT collection, not
        // retroactively just because weight now exists.
        assert_eq!(pool.undistributed, 1_000);
        collect_fee(&mut pool, 0); // no-op
        assert_eq!(pool.undistributed, 1_000);
    }

    #[test]
    fn a_bigger_balance_earns_nothing_retroactively() {
        let mut pool = new_pool();
        let mut alice = new_registration();
        do_sync(&mut alice, &mut pool, 100, 0, 0);

        collect_fee(&mut pool, 1_000);
        touch_registration(&mut alice, &mut pool);
        assert!(alice.pending_rewards > 0);

        // Bob registers and syncs AFTER that fee already landed - he must
        // capture none of it, no matter how large his balance is.
        let mut bob = new_registration();
        do_sync(&mut bob, &mut pool, 1_000_000, 100, 0);
        assert_eq!(bob.pending_rewards, 0, "Bob must not capture fee collected before he had weight");
    }

    #[test]
    fn claim_is_blocked_until_the_minimum_slot_delay_elapses() {
        let mut pool = new_pool();
        let mut reg = new_registration();
        do_sync(&mut reg, &mut pool, 1_000, 0, 10);

        assert!(!claim_is_eligible(&reg, 10));
        assert!(!claim_is_eligible(&reg, 10 + MIN_CLAIM_DELAY_SLOTS - 1));
        assert!(claim_is_eligible(&reg, 10 + MIN_CLAIM_DELAY_SLOTS));
    }

    #[test]
    fn a_same_block_balance_increase_cannot_capture_a_fee_it_did_not_earn() {
        // The flash-loan case: buy into a token that already has undistributed
        // fee sitting in it, then try to claim in the same slot.
        let mut pool = new_pool();
        let mut victim = new_registration();
        do_sync(&mut victim, &mut pool, 1_000, 0, 0);
        collect_fee(&mut pool, 10_000);

        let mut attacker = new_registration();
        do_sync(&mut attacker, &mut pool, 1_000_000, 1, 5); // same-slot balance increase
        assert!(!claim_is_eligible(&attacker, 5), "must wait out MIN_CLAIM_DELAY_SLOTS first");
    }

    #[test]
    fn sharding_registrations_never_out_earns_one_wallet() {
        // Same anti-sybil property as tenure.rs's weight-level test, proven
        // through the full touch/refresh/collect accumulator flow: one
        // wallet holding the whole balance vs N wallets holding an even
        // split, all synced at the identical time, then a single fee lands.
        let now = 700; // past the 10-minute tier
        let whole_balance = 9_000_000u64;

        let mut whole_pool = new_pool();
        let mut whole = new_registration();
        do_sync(&mut whole, &mut whole_pool, whole_balance, now, 0);
        collect_fee(&mut whole_pool, 1_000_000);
        touch_registration(&mut whole, &mut whole_pool);

        for shards in [3u64, 9, 30] {
            let mut shard_pool = new_pool();
            let mut wallets: Vec<Registration> = (0..shards)
                .map(|_| {
                    let mut r = new_registration();
                    do_sync(&mut r, &mut shard_pool, whole_balance / shards, now, 0);
                    r
                })
                .collect();
            collect_fee(&mut shard_pool, 1_000_000);
            let mut summed = 0u64;
            for r in wallets.iter_mut() {
                touch_registration(r, &mut shard_pool);
                summed += r.pending_rewards;
            }
            assert!(
                summed <= whole.pending_rewards,
                "{} shards summed to {} > whole wallet's {}",
                shards,
                summed,
                whole.pending_rewards
            );
        }
    }

    #[test]
    fn payouts_never_exceed_collected_fees_across_random_operations() {
        let mut pool = new_pool();
        let mut wallets: Vec<Registration> = (0..5)
            .map(|i| {
                let mut r = new_registration();
                r.owner = Pubkey::new_from_array([i as u8 + 1; 32]);
                r
            })
            .collect();

        let mut seed: u64 = 0xC0FFEE;
        let mut next = move || {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            seed >> 33
        };

        let mut now = 0i64;
        let mut slot = 0u64;
        let mut claimed_total = 0u64;

        for step in 0..400u64 {
            now += 1 + (next() % 200) as i64;
            slot += 1 + next() % 5;
            let i = (next() % wallets.len() as u64) as usize;

            match next() % 3 {
                0 => {
                    let fee = 1 + next() % 100_000;
                    collect_fee(&mut pool, fee);
                }
                1 => {
                    let balance = next() % 10_000_000;
                    do_sync(&mut wallets[i], &mut pool, balance, now, slot);
                }
                _ => {
                    if claim_is_eligible(&wallets[i], slot) {
                        touch_registration(&mut wallets[i], &mut pool);
                        let payout = wallets[i].pending_rewards;
                        if payout > 0 {
                            wallets[i].pending_rewards = 0;
                            wallets[i].lifetime_rewards_claimed += payout;
                            pool.total_claimed += payout;
                            claimed_total += payout;
                        }
                    }
                }
            }

            assert!(
                pool.total_claimed <= pool.total_collected,
                "step {}: total_claimed {} exceeded total_collected {}",
                step,
                pool.total_claimed,
                pool.total_collected
            );
        }

        let mut still_pending = 0u64;
        for r in wallets.iter_mut() {
            touch_registration(r, &mut pool);
            still_pending += r.pending_rewards;
        }
        assert!(
            claimed_total + still_pending <= pool.total_collected,
            "settled+pending {} exceeded collected fees {}",
            claimed_total + still_pending,
            pool.total_collected
        );
    }

    mod vault_surplus_tests {
        use super::*;

        const RENT_FLOOR: u64 = 890_880; // a real 0-byte-account rent exemption, for realism

        #[test]
        fn an_exactly_explained_balance_has_no_surplus() {
            // rent floor + one unspent marker float + pool-backed balance,
            // and nothing more.
            let actual = RENT_FLOOR + 711_280 + 4_000;
            assert_eq!(vault_surplus(actual, RENT_FLOOR, 2_500_000, 1_788_720, 10_000, 6_000), 0);
        }

        #[test]
        fn a_plain_transfer_beyond_everything_explained_is_the_surplus() {
            let explained = RENT_FLOOR + 711_280 + 4_000;
            let actual = explained + 50_000; // an untracked SOL transfer landed
            assert_eq!(
                vault_surplus(actual, RENT_FLOOR, 2_500_000, 1_788_720, 10_000, 6_000),
                50_000
            );
        }

        #[test]
        fn unspent_marker_float_is_never_swept_as_revenue() {
            // One registration's marker just landed conceptually - no rent
            // spent against it yet, so the whole marker is retained float,
            // not a surplus, even though nothing else backs the balance.
            let actual = RENT_FLOOR + 2_500_000;
            assert_eq!(vault_surplus(actual, RENT_FLOOR, 2_500_000, 0, 0, 0), 0);
        }

        #[test]
        fn unclaimed_donated_fees_are_never_swept_twice() {
            // donate() already moved this money in AND already told the pool
            // about it - reconcile must not double-count it.
            let actual = RENT_FLOOR + 100_000;
            assert_eq!(vault_surplus(actual, RENT_FLOOR, 0, 0, 100_000, 0), 0);
        }

        #[test]
        fn the_vaults_own_rent_floor_is_never_mistaken_for_revenue() {
            assert_eq!(vault_surplus(RENT_FLOOR, RENT_FLOOR, 0, 0, 0, 0), 0);
        }

        #[test]
        fn a_balance_below_what_is_explained_saturates_to_zero_rather_than_panicking() {
            // Should never happen under correct operation, but a permissionless
            // crank must never be able to panic over it.
            let actual = RENT_FLOOR; // less than what total_collected alone implies
            assert_eq!(vault_surplus(actual, RENT_FLOOR, 0, 0, 100_000, 0), 0);
        }

        #[test]
        fn a_deficit_from_markers_and_rent_alone_saturates_to_zero() {
            // Isolates the marker/rent term specifically (total_collected and
            // total_claimed are both zero here, so the pool-backed term
            // contributes nothing): the vault's real balance coming in under
            // what own_rent_floor + retained_marker_float alone implies it
            // should hold. Should never happen - write_registration always
            // credits the marker and debits the rent out of the same vault
            // balance in the same instruction, so the two can never get
            // ahead of the vault's real lamports - but `reconcile` runs
            // against whatever the actual on-chain balance is, and this must
            // saturate rather than underflow if it somehow ever did.
            let actual = 100; // far below RENT_FLOOR + one marker's float
            assert_eq!(vault_surplus(actual, RENT_FLOOR, 2_500_000, 0, 0, 0), 0);
        }

        #[test]
        fn desynced_counters_saturate_instead_of_underflowing() {
            // total_rent_spent > total_marker_deposits should never happen
            // (write_registration only ever increments them together), but
            // the function must not panic if it somehow did.
            assert_eq!(vault_surplus(RENT_FLOOR, RENT_FLOOR, 100, 200, 0, 0), 0);
            // Same for total_claimed > total_collected.
            assert_eq!(vault_surplus(RENT_FLOOR, RENT_FLOOR, 0, 0, 100, 200), 0);
        }

        #[test]
        fn many_registrations_worth_of_float_still_never_counts_as_revenue() {
            let markers = 2_500_000u64 * 10;
            let rent_spent = 1_788_720u64 * 10;
            let actual = RENT_FLOOR + (markers - rent_spent);
            assert_eq!(vault_surplus(actual, RENT_FLOOR, markers, rent_spent, 0, 0), 0);
        }
    }
}

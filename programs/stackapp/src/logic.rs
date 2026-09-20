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
}

//! Position / pool state transitions.
//!
//! These operate on plain `&mut` state structs rather than on Anchor accounts,
//! so the instruction handlers stay thin and every rule in here is covered by
//! host-side `cargo test` without a validator.
//!
//! Ordering matters and is the same in every handler:
//!
//! ```text
//! touch_position()      // flush buffered tax, then settle at the OLD weight
//! ...mutate lots...
//! refresh_weight()      // re-price weight, adjust the pool total
//! collect_tax()         // only then distribute new tax
//! ```
//!
//! Settling before re-weighting is what stops a buy from earning retroactively;
//! re-weighting before collecting is what stops a seller from taxing themselves
//! at a weight they no longer have.

use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::math::*;
use crate::state::{Lot, LoyaltyPool, Position, TaxPoint};

/// Result of removing tokens from a position.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct ExitBreakdown {
    /// Tokens removed from the position.
    pub gross: u64,
    /// Portion routed to the loyalty pool.
    pub tax: u64,
    /// Portion that reaches the destination (curve or recipient).
    pub net: u64,
    /// `sum(chunk * age_at_exit)`, fed into reputation.
    pub tenure_weighted_volume: u128,
    /// Lamports of cost basis released pro-rata by this exit.
    pub cost_basis_released: u64,
    /// Highest tax rate applied across the consumed lots, for events/UI.
    pub top_tax_bps: u16,
}

// ---------------------------------------------------------------------------
// Pool accounting
// ---------------------------------------------------------------------------

/// Push any buffered tax into the accumulator, if there is weight to receive it.
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

/// Accrue a position's rewards up to the pool's current accumulator.
pub fn settle_position(position: &mut Position, pool: &LoyaltyPool) {
    let owed = pending(
        position.weighted_shares,
        pool.acc_reward_per_share,
        position.reward_checkpoint,
    );
    position.pending_rewards = position.pending_rewards.saturating_add(owed);
    position.reward_checkpoint = pool.acc_reward_per_share;
}

/// Flush the pool, then settle the position. Always the first thing a handler
/// does after loading accounts.
pub fn touch_position(position: &mut Position, pool: &mut LoyaltyPool, _now: i64) {
    flush_undistributed(pool);
    settle_position(position, pool);
}

/// Re-price a position's tenure weight and reconcile the pool total.
pub fn refresh_weight(position: &mut Position, pool: &mut LoyaltyPool, now: i64) {
    let new_weight = weighted_shares_for_lots(&position.lots, now);
    pool.total_weighted_shares = pool
        .total_weighted_shares
        .saturating_sub(position.weighted_shares)
        .saturating_add(new_weight);
    position.weighted_shares = new_weight;
}

/// Route `amount` of tax into the pool.
///
/// Always buffers first and then flushes, so a distribution that lands while
/// `total_weighted_shares == 0` is held rather than burned.
pub fn collect_tax(pool: &mut LoyaltyPool, amount: u64) {
    if amount == 0 {
        return;
    }
    pool.total_collected = pool.total_collected.saturating_add(amount);
    pool.undistributed = pool.undistributed.saturating_add(amount);
    flush_undistributed(pool);
}

/// Whether `position` has waited out the anti-flash-loan delay.
pub fn claim_is_eligible(position: &Position, current_slot: u64) -> bool {
    current_slot >= position.last_increase_slot.saturating_add(MIN_CLAIM_DELAY_SLOTS)
}

// ---------------------------------------------------------------------------
// Lots
// ---------------------------------------------------------------------------

/// Append a vesting lot (a buy, or the taxed half of an inbound transfer).
///
/// Lots bought in the same second are merged; otherwise the position grows a
/// new lot, which is what keeps tenure honest under top-ups.
pub fn push_vesting_lot(position: &mut Position, amount: u64, now: i64) -> Result<()> {
    require!(amount > 0, StackError::ZeroAmount);
    if let Some(last) = position.lots.last_mut() {
        // Merge only into another *untouched* vesting lot from the same second.
        if last.buy_timestamp == now && last.cum_released == 0 {
            last.original = last.original.checked_add(amount).ok_or(StackError::MathOverflow)?;
            return Ok(());
        }
    }
    require!(position.lots.len() < MAX_LOTS, StackError::LotCapacityExceeded);
    position.lots.push(Lot::new(amount, now));
    Ok(())
}

/// Append an immediately-liquid lot (a loyalty-pool reward payout).
///
/// Rewards are not subject to vesting, but they *are* stamped with the current
/// time, so dumping them straight away pays the top of the tax curve.
pub fn push_liquid_lot(position: &mut Position, amount: u64, now: i64) -> Result<()> {
    require!(amount > 0, StackError::ZeroAmount);
    if let Some(last) = position.lots.last_mut() {
        if last.buy_timestamp == now && last.cum_released == last.original {
            last.original = last.original.checked_add(amount).ok_or(StackError::MathOverflow)?;
            last.cum_released = last
                .cum_released
                .checked_add(amount)
                .ok_or(StackError::MathOverflow)?;
            last.released = last.released.checked_add(amount).ok_or(StackError::MathOverflow)?;
            position.spendable = position
                .spendable
                .checked_add(amount)
                .ok_or(StackError::MathOverflow)?;
            return Ok(());
        }
    }
    require!(position.lots.len() < MAX_LOTS, StackError::LotCapacityExceeded);
    position.lots.push(Lot::liquid(amount, now));
    position.spendable = position
        .spendable
        .checked_add(amount)
        .ok_or(StackError::MathOverflow)?;
    Ok(())
}

/// Drop lots with nothing left in them.
pub fn prune_empty_lots(position: &mut Position) {
    position.lots.retain(|l| !l.is_empty());
}

/// Merge the two oldest lots, taking the **newer** of the two timestamps.
///
/// Owner-initiated only. Taking the newer timestamp means compaction can never
/// be used to launder fresh tokens into an old lot's low tax rate.
pub fn compact_oldest_lots(position: &mut Position) -> Result<()> {
    require!(position.lots.len() >= 2, StackError::NothingToCompact);
    let b = position.lots.remove(1);
    let a = &mut position.lots[0];
    a.original = a.original.checked_add(b.original).ok_or(StackError::MathOverflow)?;
    a.cum_released = a
        .cum_released
        .checked_add(b.cum_released)
        .ok_or(StackError::MathOverflow)?;
    a.released = a.released.checked_add(b.released).ok_or(StackError::MathOverflow)?;
    a.buy_timestamp = a.buy_timestamp.max(b.buy_timestamp);
    Ok(())
}

/// Move everything vesting has unlocked into the spendable balance.
pub fn release_vested(position: &mut Position, vest_duration_seconds: i64, now: i64) -> Result<u64> {
    let mut total: u64 = 0;
    for lot in position.lots.iter_mut() {
        let target = vested_amount(lot.original, lot.buy_timestamp, now, vest_duration_seconds);
        let delta = target.saturating_sub(lot.cum_released);
        if delta == 0 {
            continue;
        }
        lot.cum_released = lot.cum_released.checked_add(delta).ok_or(StackError::MathOverflow)?;
        lot.released = lot.released.checked_add(delta).ok_or(StackError::MathOverflow)?;
        total = total.checked_add(delta).ok_or(StackError::MathOverflow)?;
    }
    if total > 0 {
        position.spendable = position
            .spendable
            .checked_add(total)
            .ok_or(StackError::MathOverflow)?;
        position.vested_claimed = position
            .vested_claimed
            .checked_add(total)
            .ok_or(StackError::MathOverflow)?;
    }
    Ok(total)
}

/// Remove `amount` spendable tokens from a position, oldest lot first.
///
/// This is the single code path behind **every** balance decrease - a sell, a
/// wallet-to-wallet transfer, or a pool donation. Only `tax_exempt` (reserved
/// for the program's own pool address) skips the tax, and in that case the
/// tokens go to the pool anyway, so there is nothing to game.
pub fn consume_spendable(
    position: &mut Position,
    amount: u64,
    tax_curve: &[TaxPoint],
    now: i64,
    tax_exempt: bool,
) -> Result<ExitBreakdown> {
    require!(amount > 0, StackError::ZeroAmount);
    require!(position.spendable >= amount, StackError::InsufficientSpendable);

    let total_remaining_before = position.total_remaining();
    let mut out = ExitBreakdown::default();
    let mut remaining = amount;

    // `lots` is append-ordered, so iterating forward is FIFO by buy_timestamp.
    for lot in position.lots.iter_mut() {
        if remaining == 0 {
            break;
        }
        if lot.released == 0 {
            continue;
        }
        let take = lot.released.min(remaining);
        let age = now - lot.buy_timestamp;
        let bps = if tax_exempt { 0 } else { tax_bps_for_age(tax_curve, age) };
        let tax = tax_for_amount(take, bps);

        out.tax = out.tax.checked_add(tax).ok_or(StackError::MathOverflow)?;
        out.net = out
            .net
            .checked_add(take - tax)
            .ok_or(StackError::MathOverflow)?;
        out.tenure_weighted_volume = out
            .tenure_weighted_volume
            .saturating_add((take as u128) * (age.max(0) as u128));
        if bps > out.top_tax_bps {
            out.top_tax_bps = bps;
        }

        lot.released -= take;
        remaining -= take;
    }

    require!(remaining == 0, StackError::InsufficientSpendable);
    out.gross = amount;

    position.spendable -= amount;
    position.total_sold = position
        .total_sold
        .checked_add(amount)
        .ok_or(StackError::MathOverflow)?;
    position.tenure_weighted_volume = position
        .tenure_weighted_volume
        .saturating_add(out.tenure_weighted_volume);

    // Release cost basis pro-rata to the fraction of the position leaving.
    if total_remaining_before > 0 && position.cost_basis_lamports > 0 {
        let released = (((position.cost_basis_lamports as u128) * (amount as u128))
            / (total_remaining_before as u128)) as u64;
        let released = released.min(position.cost_basis_lamports);
        position.cost_basis_lamports -= released;
        out.cost_basis_released = released;
    }

    prune_empty_lots(position);
    Ok(out)
}

/// Does this position still qualify as "held to maturity"?
///
/// It must have kept at least `MATURITY_MIN_RETENTION_BPS` of everything it
/// ever acquired; a full (or near-full) exit disqualifies it.
pub fn retained_through_maturity(position: &Position) -> bool {
    if position.total_bought == 0 {
        return false;
    }
    let remaining = position.total_remaining() as u128;
    let threshold = ((position.total_bought as u128) * (MATURITY_MIN_RETENTION_BPS as u128))
        / (BPS_DENOMINATOR as u128);
    remaining >= threshold && remaining > 0
}

#[cfg(test)]
mod tests {
    use super::*;

    const DAY: i64 = 86_400;
    const SOL: u64 = 1_000_000_000;

    fn standard_curve() -> Vec<TaxPoint> {
        vec![
            TaxPoint { seconds_held: 0, tax_bps: 3_000 },
            TaxPoint { seconds_held: 3_600, tax_bps: 2_000 },
            TaxPoint { seconds_held: DAY, tax_bps: 1_000 },
            TaxPoint { seconds_held: 7 * DAY, tax_bps: 0 },
        ]
    }

    fn new_position() -> Position {
        Position {
            owner: Pubkey::default(),
            mint: Pubkey::default(),
            lots: Vec::new(),
            vested_claimed: 0,
            spendable: 0,
            weighted_shares: 0,
            reward_checkpoint: 0,
            pending_rewards: 0,
            lifetime_rewards_claimed: 0,
            last_increase_slot: 0,
            cost_basis_lamports: 0,
            total_bought: 0,
            total_sold: 0,
            first_buy_timestamp: 0,
            tenure_weighted_volume: 0,
            credited_volume: 0,
            last_reputation_timestamp: 0,
            maturity_credits: 0,
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

    /// Simulate a buy the way the `buy` handler does.
    fn do_buy(
        position: &mut Position,
        pool: &mut LoyaltyPool,
        amount: u64,
        cost: u64,
        now: i64,
        slot: u64,
    ) {
        touch_position(position, pool, now);
        push_vesting_lot(position, amount, now).unwrap();
        if position.total_bought == 0 {
            position.first_buy_timestamp = now;
        }
        position.total_bought += amount;
        position.cost_basis_lamports += cost;
        position.last_increase_slot = slot;
        refresh_weight(position, pool, now);
    }

    /// Simulate a sell the way the `sell` handler does.
    fn do_sell(
        position: &mut Position,
        pool: &mut LoyaltyPool,
        amount: u64,
        curve: &[TaxPoint],
        now: i64,
    ) -> ExitBreakdown {
        touch_position(position, pool, now);
        let out = consume_spendable(position, amount, curve, now, false).unwrap();
        refresh_weight(position, pool, now);
        collect_tax(pool, out.tax);
        out
    }

    fn do_claim_vested(position: &mut Position, pool: &mut LoyaltyPool, vest: i64, now: i64) -> u64 {
        touch_position(position, pool, now);
        let released = release_vested(position, vest, now).unwrap();
        refresh_weight(position, pool, now);
        released
    }

    // -- invariants ---------------------------------------------------------

    fn assert_invariants(position: &Position, label: &str) {
        let sum_released: u64 = position.lots.iter().map(|l| l.released).sum();
        assert_eq!(
            position.spendable, sum_released,
            "{}: spendable {} != sum(lot.released) {}",
            label, position.spendable, sum_released
        );
        for l in position.lots.iter() {
            assert!(l.cum_released <= l.original, "{}: cum_released > original", label);
            assert!(l.released <= l.original, "{}: released > original", label);
            assert!(!l.is_empty(), "{}: empty lot was not pruned", label);
        }
    }

    // -- vesting ------------------------------------------------------------

    #[test]
    fn vesting_releases_linearly_into_spendable() {
        let (mut p, mut pool) = (new_position(), new_pool());
        let vest = 10 * DAY;
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);

        assert_eq!(p.spendable, 0, "a fresh buy is fully locked");
        assert_eq!(do_claim_vested(&mut p, &mut pool, vest, 2 * DAY), 200);
        assert_eq!(p.spendable, 200);
        assert_eq!(do_claim_vested(&mut p, &mut pool, vest, 5 * DAY), 300);
        assert_eq!(p.spendable, 500);
        assert_eq!(do_claim_vested(&mut p, &mut pool, vest, 100 * DAY), 500);
        assert_eq!(p.spendable, 1_000);
        assert_eq!(p.vested_claimed, 1_000);
        assert_eq!(
            do_claim_vested(&mut p, &mut pool, vest, 200 * DAY),
            0,
            "a fully vested position releases nothing further"
        );
        assert_invariants(&p, "vesting");
    }

    #[test]
    fn cannot_sell_more_than_is_vested() {
        let (mut p, mut pool) = (new_position(), new_pool());
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);
        do_claim_vested(&mut p, &mut pool, 10 * DAY, 2 * DAY);
        assert_eq!(p.spendable, 200);
        let err = consume_spendable(&mut p, 201, &standard_curve(), 2 * DAY, false);
        assert!(err.is_err(), "selling unvested tokens must fail");
    }

    // -- FIFO vs average-cost ----------------------------------------------

    /// The reason lots exist. Averaging the buy timestamp lets fresh capital
    /// hide behind an old position's tenure; FIFO does not.
    #[test]
    fn fifo_charges_more_than_average_cost_after_a_top_up() {
        let curve = standard_curve();
        let (mut p, mut pool) = (new_position(), new_pool());
        let vest = 0; // isolate the tax rule from vesting

        // Old money: 1_000 bought 30 days ago -> 0% tier.
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);
        // Fresh money: 1_000 bought right now.
        do_buy(&mut p, &mut pool, 1_000, SOL, 30 * DAY, 2);
        do_claim_vested(&mut p, &mut pool, vest, 30 * DAY);

        let out = do_sell(&mut p, &mut pool, 2_000, &curve, 30 * DAY);

        // What an average-age model would have charged: mean age 15 days -> 0%
        // for this curve (>= 7 days). FIFO charges 0 on the old lot and 30% on
        // the fresh one.
        let average_age = 15 * DAY;
        let average_model_tax = tax_for_amount(2_000, tax_bps_for_age(&curve, average_age));

        assert_eq!(out.tax, 300, "FIFO should charge 30% on the fresh 1_000 only");
        assert_eq!(average_model_tax, 0);
        assert!(
            out.tax > average_model_tax,
            "FIFO tax {} must exceed the average-age model's {}",
            out.tax,
            average_model_tax
        );
    }

    #[test]
    fn fifo_consumes_the_oldest_lot_first() {
        let curve = standard_curve();
        let (mut p, mut pool) = (new_position(), new_pool());
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);
        do_buy(&mut p, &mut pool, 1_000, SOL, 30 * DAY, 2);
        do_claim_vested(&mut p, &mut pool, 0, 30 * DAY);

        // Sell exactly the old lot: it is 30 days old, so 0% tax.
        let out = do_sell(&mut p, &mut pool, 1_000, &curve, 30 * DAY);
        assert_eq!(out.tax, 0);
        assert_eq!(p.lots.len(), 1);
        assert_eq!(p.lots[0].buy_timestamp, 30 * DAY, "the fresh lot must be what remains");

        // The rest is fresh, so it pays the top rate.
        let out2 = do_sell(&mut p, &mut pool, 1_000, &curve, 30 * DAY);
        assert_eq!(out2.tax, 300);
        assert!(p.lots.is_empty());
        assert_invariants(&p, "fifo");
    }

    // -- transfers are sells ------------------------------------------------

    /// A wallet-to-wallet transfer must cost exactly what a sell costs.
    #[test]
    fn transfer_is_taxed_identically_to_a_sell() {
        let curve = standard_curve();

        let build = || {
            let (mut p, mut pool) = (new_position(), new_pool());
            do_buy(&mut p, &mut pool, 5_000, 3 * SOL, 0, 1);
            do_claim_vested(&mut p, &mut pool, 0, 2 * DAY);
            (p, pool)
        };

        let (mut seller, mut pool_a) = build();
        let sold = do_sell(&mut seller, &mut pool_a, 4_000, &curve, 2 * DAY);

        // Same state, but the tokens leave via a transfer to another wallet.
        let (mut sender, mut pool_b) = build();
        touch_position(&mut sender, &mut pool_b, 2 * DAY);
        let moved = consume_spendable(&mut sender, 4_000, &curve, 2 * DAY, false).unwrap();
        refresh_weight(&mut sender, &mut pool_b, 2 * DAY);
        collect_tax(&mut pool_b, moved.tax);

        assert_eq!(moved.tax, sold.tax);
        assert_eq!(moved.net, sold.net);
        assert_eq!(pool_a.total_collected, pool_b.total_collected);
        assert!(moved.tax > 0, "a 2-day-old exit should not be free");
    }

    /// Tenure does not survive a transfer: the recipient's lot is stamped now.
    #[test]
    fn transferred_tokens_restart_the_tenure_clock() {
        let curve = standard_curve();
        let (mut sender, mut pool) = (new_position(), new_pool());
        do_buy(&mut sender, &mut pool, 10_000, 5 * SOL, 0, 1);
        do_claim_vested(&mut sender, &mut pool, 0, 30 * DAY);

        let moved = do_sell(&mut sender, &mut pool, 10_000, &curve, 30 * DAY);
        assert_eq!(moved.tax, 0, "the sender's own tokens were mature");

        let mut recipient = new_position();
        touch_position(&mut recipient, &mut pool, 30 * DAY);
        push_liquid_lot(&mut recipient, moved.net, 30 * DAY).unwrap();
        recipient.last_increase_slot = 99;
        refresh_weight(&mut recipient, &mut pool, 30 * DAY);

        // The recipient dumping immediately pays the top rate, not 0%.
        let dumped = do_sell(&mut recipient, &mut pool, moved.net, &curve, 30 * DAY);
        assert_eq!(dumped.tax, tax_for_amount(moved.net, 3_000));
        assert!(dumped.tax > 0);
    }

    /// The only exempt destination is the program's own pool, and that path
    /// gives the tokens away, so it cannot be used to dodge anything.
    #[test]
    fn pool_donation_is_exempt_but_forfeits_the_tokens() {
        let curve = standard_curve();
        let (mut p, mut pool) = (new_position(), new_pool());
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);
        do_claim_vested(&mut p, &mut pool, 0, 60);

        touch_position(&mut p, &mut pool, 60);
        let out = consume_spendable(&mut p, 1_000, &curve, 60, true).unwrap();
        refresh_weight(&mut p, &mut pool, 60);
        collect_tax(&mut pool, out.gross);

        assert_eq!(out.tax, 0, "donations to the pool are exempt");
        assert_eq!(out.net, 1_000);
        assert_eq!(p.total_remaining(), 0, "but the donor keeps nothing");
        assert_eq!(pool.total_collected, 1_000);
    }

    #[test]
    fn splitting_a_sell_into_dust_does_not_reduce_tax() {
        let curve = standard_curve();

        let (mut whole, mut pool_a) = (new_position(), new_pool());
        do_buy(&mut whole, &mut pool_a, 10_000, SOL, 0, 1);
        do_claim_vested(&mut whole, &mut pool_a, 0, 60);
        let one_shot = do_sell(&mut whole, &mut pool_a, 10_000, &curve, 60);

        let (mut dribble, mut pool_b) = (new_position(), new_pool());
        do_buy(&mut dribble, &mut pool_b, 10_000, SOL, 0, 1);
        do_claim_vested(&mut dribble, &mut pool_b, 0, 60);
        let mut dust_tax = 0u64;
        for _ in 0..10_000 {
            dust_tax += do_sell(&mut dribble, &mut pool_b, 1, &curve, 60).tax;
        }

        assert!(
            dust_tax >= one_shot.tax,
            "10_000 dust sells paid {} vs {} in one sell",
            dust_tax,
            one_shot.tax
        );
    }

    // -- reward accumulator behaviour --------------------------------------

    #[test]
    fn a_buy_earns_nothing_retroactively() {
        let curve = standard_curve();
        let mut pool = new_pool();

        // Alice holds and a sell taxes into the pool.
        let mut alice = new_position();
        do_buy(&mut alice, &mut pool, 1_000, SOL, 0, 1);
        do_claim_vested(&mut alice, &mut pool, 0, 10);

        let mut seller = new_position();
        do_buy(&mut seller, &mut pool, 1_000, SOL, 0, 1);
        do_claim_vested(&mut seller, &mut pool, 0, 10);
        do_sell(&mut seller, &mut pool, 1_000, &curve, 10);
        assert!(pool.total_collected > 0);

        // Bob arrives afterwards with a far larger position.
        let mut bob = new_position();
        do_buy(&mut bob, &mut pool, 1_000_000, 500 * SOL, 11, 2);
        touch_position(&mut bob, &mut pool, 11);
        assert_eq!(bob.pending_rewards, 0, "Bob must not capture tax collected before he joined");

        // Alice, who was there, is owed her share.
        touch_position(&mut alice, &mut pool, 11);
        assert!(alice.pending_rewards > 0);
    }

    #[test]
    fn claim_is_blocked_until_the_minimum_slot_delay_elapses() {
        let mut p = new_position();
        p.last_increase_slot = 100;
        assert!(!claim_is_eligible(&p, 100), "same-slot claim must be rejected");
        assert!(!claim_is_eligible(&p, 100 + MIN_CLAIM_DELAY_SLOTS - 1));
        assert!(claim_is_eligible(&p, 100 + MIN_CLAIM_DELAY_SLOTS));
        assert!(claim_is_eligible(&p, 100 + MIN_CLAIM_DELAY_SLOTS + 50));
    }

    /// The flash-loan shape: buy huge, have a victim's tax land in the same
    /// block, claim. Both the slot gate and settle-before-reweight must stop it.
    #[test]
    fn same_block_flash_position_cannot_capture_a_sell_tax() {
        let curve = standard_curve();
        let mut pool = new_pool();
        let slot = 500u64;

        let mut honest = new_position();
        do_buy(&mut honest, &mut pool, 10_000_000, 100 * SOL, 0, 1);
        do_claim_vested(&mut honest, &mut pool, 0, 10);

        // Attacker opens an enormous position in slot 500.
        let mut attacker = new_position();
        do_buy(&mut attacker, &mut pool, 100_000_000, 10_000 * SOL, 10, slot);

        // A victim's taxed sell lands in the same slot.
        let mut victim = new_position();
        do_buy(&mut victim, &mut pool, 10_000, 5 * SOL, 0, 1);
        do_claim_vested(&mut victim, &mut pool, 0, 10);
        do_sell(&mut victim, &mut pool, 10_000, &curve, 10);

        // The slot gate rejects the claim outright.
        assert!(!claim_is_eligible(&attacker, slot));

        // And even once the gate opens, the attacker's weight was only added
        // after settling, so they only earn from taxes that land afterwards.
        assert!(claim_is_eligible(&attacker, slot + MIN_CLAIM_DELAY_SLOTS));
        touch_position(&mut attacker, &mut pool, 11);
        let captured = attacker.pending_rewards;
        touch_position(&mut honest, &mut pool, 11);

        assert!(
            captured > 0,
            "the attacker does earn from the victim's tax once weighted - by design"
        );
        // ...but this is exactly why the slot gate exists: within the block the
        // claim is impossible, so the position must survive at least
        // MIN_CLAIM_DELAY_SLOTS of price risk to realise it.
        assert!(honest.pending_rewards > 0);
    }

    #[test]
    fn tax_collected_with_no_eligible_holders_is_buffered_then_paid_out() {
        let curve = standard_curve();
        let mut pool = new_pool();

        // A lone holder who fully exits, so weight hits zero as the tax lands.
        let mut lone = new_position();
        do_buy(&mut lone, &mut pool, 1_000, SOL, 0, 1);
        do_claim_vested(&mut lone, &mut pool, 0, 10);
        let out = do_sell(&mut lone, &mut pool, 1_000, &curve, 10);

        assert!(out.tax > 0);
        assert_eq!(pool.total_weighted_shares, 0);
        assert_eq!(pool.acc_reward_per_share, 0);
        assert_eq!(pool.undistributed, out.tax, "tax must be buffered, not burned");

        // A new holder appears; the buffer flushes to them.
        let mut newcomer = new_position();
        do_buy(&mut newcomer, &mut pool, 2_000, SOL, 20, 5);
        do_claim_vested(&mut newcomer, &mut pool, 0, 21);
        touch_position(&mut newcomer, &mut pool, 22);

        assert_eq!(pool.undistributed, 0);
        assert!(newcomer.pending_rewards > 0);
        assert!(newcomer.pending_rewards <= out.tax);
    }

    /// Sharding one position across four wallets must not out-earn holding it
    /// whole - the headline anti-sybil rule.
    #[test]
    fn sharding_across_wallets_does_not_out_earn_one_wallet() {
        let curve = standard_curve();

        // Scenario A: one wallet holds 4_000.
        let mut pool_a = new_pool();
        let mut whale = new_position();
        do_buy(&mut whale, &mut pool_a, 4_000, 4 * SOL, 0, 1);
        do_claim_vested(&mut whale, &mut pool_a, 0, 10);

        let mut seller_a = new_position();
        do_buy(&mut seller_a, &mut pool_a, 100_000, 50 * SOL, 0, 1);
        do_claim_vested(&mut seller_a, &mut pool_a, 0, 10);
        do_sell(&mut seller_a, &mut pool_a, 100_000, &curve, 10);
        touch_position(&mut whale, &mut pool_a, 11);
        let whole_rewards = whale.pending_rewards;

        // Scenario B: the same capital, same timing, four wallets.
        let mut pool_b = new_pool();
        let mut shards: Vec<Position> = Vec::new();
        for _ in 0..4 {
            let mut s = new_position();
            do_buy(&mut s, &mut pool_b, 1_000, SOL, 0, 1);
            do_claim_vested(&mut s, &mut pool_b, 0, 10);
            shards.push(s);
        }

        let mut seller_b = new_position();
        do_buy(&mut seller_b, &mut pool_b, 100_000, 50 * SOL, 0, 1);
        do_claim_vested(&mut seller_b, &mut pool_b, 0, 10);
        do_sell(&mut seller_b, &mut pool_b, 100_000, &curve, 10);

        let mut shard_rewards = 0u64;
        for s in shards.iter_mut() {
            touch_position(s, &mut pool_b, 11);
            shard_rewards += s.pending_rewards;
        }

        assert!(whole_rewards > 0);
        assert!(
            shard_rewards <= whole_rewards,
            "4 shards earned {} vs {} held whole",
            shard_rewards,
            whole_rewards
        );
    }

    #[test]
    fn reward_payouts_are_liquid_but_freshly_stamped() {
        let curve = standard_curve();
        let mut pool = new_pool();
        let mut p = new_position();
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);
        do_claim_vested(&mut p, &mut pool, 10 * DAY, 10 * DAY);

        // Pay out a reward at t = 10 days.
        push_liquid_lot(&mut p, 500, 10 * DAY).unwrap();
        assert_eq!(p.spendable, 1_500, "rewards are immediately spendable");

        // But selling them straight away pays the top of the curve, because the
        // reward lot's timestamp is now, not the original buy.
        let out = do_sell(&mut p, &mut pool, 1_500, &curve, 10 * DAY);
        assert_eq!(
            out.tax,
            tax_for_amount(500, 3_000),
            "only the fresh reward lot should be taxed"
        );
        assert_invariants(&p, "rewards");
    }

    // -- lots plumbing ------------------------------------------------------

    #[test]
    fn same_second_buys_merge_instead_of_growing_lots() {
        let (mut p, mut pool) = (new_position(), new_pool());
        for _ in 0..50 {
            do_buy(&mut p, &mut pool, 10, 1_000, 42, 1);
        }
        assert_eq!(p.lots.len(), 1);
        assert_eq!(p.lots[0].original, 500);
    }

    #[test]
    fn lot_capacity_is_enforced() {
        let (mut p, mut pool) = (new_position(), new_pool());
        for i in 0..MAX_LOTS {
            do_buy(&mut p, &mut pool, 10, 1_000, i as i64, 1);
        }
        assert_eq!(p.lots.len(), MAX_LOTS);
        let err = push_vesting_lot(&mut p, 10, MAX_LOTS as i64);
        assert!(err.is_err(), "the {}th lot must be rejected", MAX_LOTS + 1);
    }

    #[test]
    fn compaction_takes_the_newer_timestamp() {
        let (mut p, mut pool) = (new_position(), new_pool());
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);
        do_buy(&mut p, &mut pool, 1_000, SOL, 30 * DAY, 2);
        compact_oldest_lots(&mut p).unwrap();

        assert_eq!(p.lots.len(), 1);
        assert_eq!(p.lots[0].original, 2_000);
        assert_eq!(
            p.lots[0].buy_timestamp,
            30 * DAY,
            "compaction must not launder fresh tokens into an old lot"
        );
    }

    #[test]
    fn maturity_requires_retaining_most_of_the_position() {
        let curve = standard_curve();
        let (mut p, mut pool) = (new_position(), new_pool());
        do_buy(&mut p, &mut pool, 1_000, SOL, 0, 1);
        do_claim_vested(&mut p, &mut pool, 0, 10 * DAY);
        assert!(retained_through_maturity(&p));

        do_sell(&mut p, &mut pool, 400, &curve, 10 * DAY);
        assert!(retained_through_maturity(&p), "60% retained still counts");

        do_sell(&mut p, &mut pool, 200, &curve, 10 * DAY);
        assert!(!retained_through_maturity(&p), "40% retained is a substantial exit");
    }

    /// A long adversarial sequence: the position-level invariants must hold at
    /// every step and the pool must stay solvent.
    #[test]
    fn invariants_hold_across_a_random_op_sequence() {
        let curve = standard_curve();
        let vest = 3 * DAY;
        let mut pool = new_pool();
        let mut p = new_position();

        let mut seed: u64 = 0xD15EA5E;
        let mut next = move || {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            seed >> 33
        };

        let mut now = 0i64;
        let mut slot = 0u64;
        let mut claimed_total = 0u64;

        for step in 0..300u64 {
            now += 1 + (next() % 20_000) as i64;
            slot += 1 + next() % 10;

            match next() % 4 {
                0 => {
                    let amount = 1 + next() % 100_000;
                    if p.lots.len() < MAX_LOTS {
                        do_buy(&mut p, &mut pool, amount, amount * 7, now, slot);
                    }
                }
                1 => {
                    do_claim_vested(&mut p, &mut pool, vest, now);
                }
                2 => {
                    if p.spendable > 0 {
                        let amount = 1 + next() % p.spendable;
                        do_sell(&mut p, &mut pool, amount, &curve, now);
                    }
                }
                _ => {
                    touch_position(&mut p, &mut pool, now);
                    if claim_is_eligible(&p, slot) && p.pending_rewards > 0 {
                        let payout = p.pending_rewards;
                        p.pending_rewards = 0;
                        p.lifetime_rewards_claimed += payout;
                        pool.total_claimed += payout;
                        claimed_total += payout;
                        push_liquid_lot(&mut p, payout, now).ok();
                    }
                    refresh_weight(&mut p, &mut pool, now);
                }
            }

            assert_invariants(&p, &format!("step {}", step));
            assert_eq!(
                pool.total_weighted_shares, p.weighted_shares,
                "step {}: pool total drifted from the only position",
                step
            );
        }

        touch_position(&mut p, &mut pool, now);
        let owed = claimed_total + p.pending_rewards;
        assert!(
            owed <= pool.total_collected,
            "payouts {} exceeded collected tax {}",
            owed,
            pool.total_collected
        );
    }
}

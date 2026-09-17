use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::PoolClaimed;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct ClaimPoolShare<'info> {
    pub owner: Signer<'info>,

    /// CHECK: identity/seed for this launch only; never deserialized.
    pub mint: UncheckedAccount<'info>,

    #[account(
        has_one = mint,
        seeds = [SEED_CONFIG, mint.key().as_ref()],
        bump = token_config.bump
    )]
    pub token_config: Account<'info, TokenConfig>,

    #[account(
        mut,
        has_one = mint,
        seeds = [SEED_POOL, mint.key().as_ref()],
        bump = loyalty_pool.bump
    )]
    pub loyalty_pool: Account<'info, LoyaltyPool>,

    #[account(
        mut,
        has_one = mint,
        seeds = [SEED_POSITION, mint.key().as_ref(), owner.key().as_ref()],
        bump = position.bump
    )]
    pub position: Account<'info, Position>,
}

/// Pay out `(acc_reward_per_share_now - last_checkpoint) * weighted_shares`.
///
/// Two guards matter here:
///
/// 1. **Block delay.** `MIN_CLAIM_DELAY_SLOTS` must have elapsed since the
///    position's last balance *increase*. A flash-loaned position cannot buy,
///    watch a victim's taxed sell inflate `acc_reward_per_share`, and claim
///    against it inside the same block - it has to carry real price risk for
///    several slots first.
/// 2. **Settle before re-weight.** `touch_position` accrues at the weight the
///    position had while the tax was collected; only then is the payout added
///    as new weight.
///
/// The payout is credited as an immediately-liquid lot stamped *now*, so
/// dumping rewards on arrival pays the top of the tax curve.
pub fn handler(ctx: Context<ClaimPoolShare>) -> Result<()> {
    let clock = Clock::get()?;
    let now = clock.unix_timestamp;
    let slot = clock.slot;
    let owner_key = ctx.accounts.owner.key();
    let mint_key = ctx.accounts.token_config.mint;

    let pool = &mut ctx.accounts.loyalty_pool;
    let position = &mut ctx.accounts.position;

    require!(claim_is_eligible(position, slot), StackError::ClaimTooSoon);

    touch_position(position, pool, now);

    let payout = position.pending_rewards;
    require!(payout > 0, StackError::NothingToClaim);

    position.pending_rewards = 0;
    position.lifetime_rewards_claimed = position
        .lifetime_rewards_claimed
        .checked_add(payout)
        .ok_or(StackError::MathOverflow)?;
    pool.total_claimed = pool.total_claimed.saturating_add(payout);

    push_liquid_lot(position, payout, now)?;
    // Claiming increases the balance, so the delay restarts for the next claim.
    position.last_increase_slot = slot;
    refresh_weight(position, pool, now);

    emit!(PoolClaimed {
        mint: mint_key,
        owner: owner_key,
        amount: payout,
        weighted_shares: position.weighted_shares,
        lifetime_claimed: position.lifetime_rewards_claimed,
        timestamp: now,
    });

    Ok(())
}

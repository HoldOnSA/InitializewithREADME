use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::VestedClaimed;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct ClaimVested<'info> {
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

/// Move everything linear vesting has unlocked into the spendable balance.
///
/// This does not change the position's size or its tenure, so it does not
/// change pool weight - it only decides what the owner is allowed to sell.
pub fn handler(ctx: Context<ClaimVested>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let vest = ctx.accounts.token_config.vest_duration_seconds;
    let mint_key = ctx.accounts.token_config.mint;
    let owner_key = ctx.accounts.owner.key();

    let pool = &mut ctx.accounts.loyalty_pool;
    let position = &mut ctx.accounts.position;

    touch_position(position, pool, now);
    let released = release_vested(position, vest, now)?;
    require!(released > 0, StackError::NothingToClaim);
    refresh_weight(position, pool, now);

    emit!(VestedClaimed {
        mint: mint_key,
        owner: owner_key,
        released,
        spendable_total: position.spendable,
        locked_total: position.total_locked(),
        timestamp: now,
    });

    Ok(())
}

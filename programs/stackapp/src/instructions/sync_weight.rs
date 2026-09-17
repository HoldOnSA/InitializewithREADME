use anchor_lang::prelude::*;

use crate::constants::*;
use crate::events::WeightSynced;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct SyncWeight<'info> {
    /// Permissionless. Anyone may poke a stale position.
    pub cranker: Signer<'info>,

    /// CHECK: identity/seed for this launch only; never deserialized.
    pub mint: UncheckedAccount<'info>,

    /// CHECK: the position owner, used only to derive the position PDA.
    pub owner: UncheckedAccount<'info>,

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

/// Re-price a position's tenure weight.
///
/// Weight is a step function of age, so it only needs to change when a position
/// crosses a tenure tier. Rather than have the program discover that (which
/// would mean iterating over holders), the position is re-priced whenever it is
/// touched - and this permissionless crank exists so a position that sits
/// untouched for months still gets its tier bump applied.
///
/// Settling before re-weighting means the crank can never move rewards that
/// were already earned at the old weight.
pub fn handler(ctx: Context<SyncWeight>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.token_config.mint;

    let pool = &mut ctx.accounts.loyalty_pool;
    let position = &mut ctx.accounts.position;

    let previous_weight = position.weighted_shares;
    touch_position(position, pool, now);
    refresh_weight(position, pool, now);

    emit!(WeightSynced {
        mint: mint_key,
        owner: position.owner,
        previous_weight,
        new_weight: position.weighted_shares,
        total_weighted_shares: pool.total_weighted_shares,
        timestamp: now,
    });

    Ok(())
}

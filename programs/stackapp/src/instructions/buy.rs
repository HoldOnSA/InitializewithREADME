use anchor_lang::prelude::*;
use anchor_lang::system_program;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::BuyExecuted;
use crate::logic::*;
use crate::math::{buy_cost_lamports, spot_price_lamports};
use crate::state::*;

#[derive(Accounts)]
pub struct Buy<'info> {
    #[account(mut)]
    pub buyer: Signer<'info>,

    /// CHECK: identity/seed for this launch only; never deserialized.
    pub mint: UncheckedAccount<'info>,

    #[account(
        mut,
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
        init_if_needed,
        payer = buyer,
        space = 8 + Position::INIT_SPACE,
        seeds = [SEED_POSITION, mint.key().as_ref(), buyer.key().as_ref()],
        bump
    )]
    pub position: Account<'info, Position>,

    #[account(
        mut,
        has_one = mint,
        seeds = [SEED_CURVE_VAULT, mint.key().as_ref()],
        bump = token_config.curve_vault_bump
    )]
    pub curve_vault: Account<'info, CurveVault>,

    pub system_program: Program<'info, System>,
}

/// `max_cost_lamports == 0` disables the slippage check.
pub fn handler(ctx: Context<Buy>, amount: u64, max_cost_lamports: u64) -> Result<()> {
    require!(amount > 0, StackError::ZeroAmount);

    let clock = Clock::get()?;
    let now = clock.unix_timestamp;
    let slot = clock.slot;
    let buyer_key = ctx.accounts.buyer.key();
    let mint_key = ctx.accounts.token_config.mint;
    let position_bump = ctx.bumps.position;

    let cost = buy_cost_lamports(
        ctx.accounts.token_config.virtual_sol_reserves,
        ctx.accounts.token_config.virtual_token_reserves,
        amount,
    )
    .ok_or(StackError::CurveCapacityExceeded)?;
    require!(
        max_cost_lamports == 0 || cost <= max_cost_lamports,
        StackError::SlippageExceeded
    );

    // Pay the curve first; everything after this point is bookkeeping.
    system_program::transfer(
        CpiContext::new(
            ctx.accounts.system_program.to_account_info(),
            system_program::Transfer {
                from: ctx.accounts.buyer.to_account_info(),
                to: ctx.accounts.curve_vault.to_account_info(),
            },
        ),
        cost,
    )?;

    let config = &mut ctx.accounts.token_config;
    let pool = &mut ctx.accounts.loyalty_pool;
    let position = &mut ctx.accounts.position;

    // `init_if_needed` zeroes a fresh account, so a default owner means new.
    let is_new = position.owner == Pubkey::default();
    if is_new {
        position.owner = buyer_key;
        position.mint = mint_key;
        position.bump = position_bump;
        position.lots = Vec::new();
        position.first_buy_timestamp = now;
    }
    let was_empty = position.total_remaining() == 0;

    // Flush buffered tax and settle rewards at the OLD weight, before this
    // buy's weight is added. This is what makes a fresh buy non-retroactive.
    touch_position(position, pool, now);

    push_vesting_lot(position, amount, now)?;
    position.total_bought = position
        .total_bought
        .checked_add(amount)
        .ok_or(StackError::MathOverflow)?;
    position.cost_basis_lamports = position
        .cost_basis_lamports
        .checked_add(cost)
        .ok_or(StackError::MathOverflow)?;
    // Any balance increase restarts the pool-claim delay.
    position.last_increase_slot = slot;

    refresh_weight(position, pool, now);

    config.virtual_sol_reserves = config
        .virtual_sol_reserves
        .checked_add(cost)
        .ok_or(StackError::MathOverflow)?;
    config.virtual_token_reserves = config
        .virtual_token_reserves
        .checked_sub(amount)
        .ok_or(StackError::MathOverflow)?;
    config.real_sol_reserves = config
        .real_sol_reserves
        .checked_add(cost)
        .ok_or(StackError::MathOverflow)?;
    config.tokens_sold = config
        .tokens_sold
        .checked_add(amount)
        .ok_or(StackError::MathOverflow)?;
    config.total_buy_volume_tokens = config.total_buy_volume_tokens.saturating_add(amount);
    if was_empty {
        config.holder_count = config.holder_count.saturating_add(1);
    }

    emit!(BuyExecuted {
        mint: mint_key,
        buyer: buyer_key,
        amount,
        cost_lamports: cost,
        timestamp: now,
        slot,
        position_total: position.total_remaining(),
        spot_price_lamports: spot_price_lamports(
            config.virtual_sol_reserves,
            config.virtual_token_reserves,
            config.decimals
        ),
        tokens_sold: config.tokens_sold,
    });

    Ok(())
}

use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::LaunchInitialized;
use crate::math::validate_tax_curve;
use crate::state::*;

/// Prototype defaults for the virtual curve: 30 SOL against ~1.073e9 whole
/// tokens at 6 decimals, which opens at roughly 28 lamports per token.
pub const DEFAULT_VIRTUAL_SOL_RESERVES: u64 = 30_000_000_000;
pub const DEFAULT_VIRTUAL_TOKEN_RESERVES: u64 = 1_073_000_000_000_000;

#[derive(Accounts)]
pub struct InitializeLaunch<'info> {
    #[account(mut)]
    pub creator: Signer<'info>,

    /// CHECK: Used only as the identity/seed for this launch. The prototype
    /// keeps balances in `Position` accounts rather than SPL token accounts,
    /// so the program never needs to deserialize the mint. See README,
    /// "Custody model".
    pub mint: UncheckedAccount<'info>,

    #[account(
        init,
        payer = creator,
        space = 8 + TokenConfig::INIT_SPACE,
        seeds = [SEED_CONFIG, mint.key().as_ref()],
        bump
    )]
    pub token_config: Account<'info, TokenConfig>,

    #[account(
        init,
        payer = creator,
        space = 8 + LoyaltyPool::INIT_SPACE,
        seeds = [SEED_POOL, mint.key().as_ref()],
        bump
    )]
    pub loyalty_pool: Account<'info, LoyaltyPool>,

    #[account(
        init,
        payer = creator,
        space = 8 + CurveVault::INIT_SPACE,
        seeds = [SEED_CURVE_VAULT, mint.key().as_ref()],
        bump
    )]
    pub curve_vault: Account<'info, CurveVault>,

    pub system_program: Program<'info, System>,
}

pub fn handler(
    ctx: Context<InitializeLaunch>,
    tax_curve: Vec<TaxPoint>,
    vest_duration_seconds: i64,
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    decimals: u8,
) -> Result<()> {
    validate_tax_curve(&tax_curve)?;
    require!(
        vest_duration_seconds >= 0 && vest_duration_seconds <= MAX_VEST_DURATION_SECONDS,
        StackError::InvalidVestDuration
    );

    // 0 means "use the prototype default".
    let v_sol = if virtual_sol_reserves == 0 {
        DEFAULT_VIRTUAL_SOL_RESERVES
    } else {
        virtual_sol_reserves
    };
    let v_tok = if virtual_token_reserves == 0 {
        DEFAULT_VIRTUAL_TOKEN_RESERVES
    } else {
        virtual_token_reserves
    };
    require!(v_sol > 0 && v_tok > 0, StackError::ZeroAmount);

    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.mint.key();

    let opening_tax_bps = tax_curve[0].tax_bps;
    let floor_tax_bps = tax_curve[tax_curve.len() - 1].tax_bps;
    let curve_len = tax_curve.len() as u8;

    let config = &mut ctx.accounts.token_config;
    config.mint = mint_key;
    config.creator = ctx.accounts.creator.key();
    config.launch_timestamp = now;
    config.vest_duration_seconds = vest_duration_seconds;
    config.pool_account = ctx.accounts.loyalty_pool.key();
    config.curve_vault = ctx.accounts.curve_vault.key();
    config.tax_curve = tax_curve;
    config.virtual_sol_reserves = v_sol;
    config.virtual_token_reserves = v_tok;
    config.real_sol_reserves = 0;
    config.tokens_sold = 0;
    config.total_buy_volume_tokens = 0;
    config.total_sell_volume_tokens = 0;
    config.holder_count = 0;
    config.decimals = decimals;
    config.bump = ctx.bumps.token_config;
    config.curve_vault_bump = ctx.bumps.curve_vault;

    let pool = &mut ctx.accounts.loyalty_pool;
    pool.mint = mint_key;
    pool.acc_reward_per_share = 0;
    pool.total_weighted_shares = 0;
    pool.total_collected = 0;
    pool.total_claimed = 0;
    pool.undistributed = 0;
    pool.bump = ctx.bumps.loyalty_pool;

    let vault = &mut ctx.accounts.curve_vault;
    vault.mint = mint_key;
    vault.bump = ctx.bumps.curve_vault;

    emit!(LaunchInitialized {
        mint: mint_key,
        creator: ctx.accounts.creator.key(),
        launch_timestamp: now,
        vest_duration_seconds,
        virtual_sol_reserves: v_sol,
        virtual_token_reserves: v_tok,
        tax_curve_points: curve_len,
        opening_tax_bps,
        floor_tax_bps,
    });

    Ok(())
}

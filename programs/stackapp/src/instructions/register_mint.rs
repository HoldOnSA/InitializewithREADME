use anchor_lang::prelude::*;

use crate::constants::*;
use crate::events::MintRegistered;
use crate::state::*;

#[derive(Accounts)]
pub struct RegisterMint<'info> {
    /// Pays rent for the three new accounts below. There's no existing pool
    /// of funds for a brand-new token to draw on the way `write_registration`
    /// draws on `DepositVault` later.
    #[account(mut)]
    pub authority: Signer<'info>,

    #[account(
        seeds = [SEED_GLOBAL_CONFIG],
        bump = global_config.bump,
        has_one = authority
    )]
    pub global_config: Account<'info, GlobalConfig>,

    /// CHECK: identity/seed for this token only; a real pump.fun mint, but
    /// never deserialized here - StackApp doesn't touch the mint itself.
    pub mint: UncheckedAccount<'info>,

    #[account(
        init,
        payer = authority,
        space = 8 + TokenConfig::INIT_SPACE,
        seeds = [SEED_CONFIG, mint.key().as_ref()],
        bump
    )]
    pub token_config: Account<'info, TokenConfig>,

    #[account(
        init,
        payer = authority,
        space = 8 + LoyaltyPool::INIT_SPACE,
        seeds = [SEED_POOL, mint.key().as_ref()],
        bump
    )]
    pub loyalty_pool: Account<'info, LoyaltyPool>,

    #[account(
        init,
        payer = authority,
        space = 8 + DepositVault::INIT_SPACE,
        seeds = [SEED_DEPOSIT_VAULT, mint.key().as_ref()],
        bump
    )]
    pub deposit_vault: Account<'info, DepositVault>,

    pub system_program: Program<'info, System>,
}

/// Start tracking a pump.fun mint.
///
/// Authority-gated: the backend calls this once it has verified, off chain
/// (via pump.fun's own UI/API), that the token's creator actually configured
/// `deposit_vault`'s address as one of their up-to-10 fee-sharing
/// recipients. The program has no way to check that itself - see
/// `SECURITY_NOTES.md`.
pub fn handler(ctx: Context<RegisterMint>, creator: Pubkey) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.mint.key();

    let config = &mut ctx.accounts.token_config;
    config.mint = mint_key;
    config.creator = creator;
    config.registered_at = now;
    config.deposit_vault = ctx.accounts.deposit_vault.key();
    config.deposit_bump = ctx.bumps.deposit_vault;
    config.bump = ctx.bumps.token_config;

    let pool = &mut ctx.accounts.loyalty_pool;
    pool.mint = mint_key;
    pool.acc_reward_per_share = 0;
    pool.total_weighted_shares = 0;
    pool.total_collected = 0;
    pool.total_claimed = 0;
    pool.undistributed = 0;
    pool.bump = ctx.bumps.loyalty_pool;

    let vault = &mut ctx.accounts.deposit_vault;
    vault.mint = mint_key;
    vault.bump = ctx.bumps.deposit_vault;

    emit!(MintRegistered {
        mint: mint_key,
        creator,
        deposit_vault: config.deposit_vault,
        registered_at: now,
    });

    Ok(())
}

use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::invoke;
use anchor_lang::solana_program::system_instruction;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::FeeCollected;
use crate::logic::collect_fee;
use crate::state::*;

#[derive(Accounts)]
pub struct Donate<'info> {
    /// Permissionless: anyone may top up a token's pool. In practice this is
    /// meant to be the creator, routing in whatever they claimed a moment
    /// earlier via pump.fun's own `collect_creator_fee` - but nothing here
    /// checks who `donor` is, or where their lamports came from.
    #[account(mut)]
    pub donor: Signer<'info>,

    /// CHECK: identity/seed for this token only.
    pub mint: UncheckedAccount<'info>,

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
        seeds = [SEED_DEPOSIT_VAULT, mint.key().as_ref()],
        bump = deposit_vault.bump
    )]
    pub deposit_vault: Account<'info, DepositVault>,

    pub system_program: Program<'info, System>,
}

/// Voluntarily route `amount` lamports into a token's `LoyaltyPool`.
///
/// This is StackApp's only source of real fee revenue. There is no
/// automatic pull from pump.fun (see `SECURITY_NOTES.md` for why the
/// original "list `DepositVault` as a pump.fun fee-sharing shareholder"
/// plan doesn't actually work): fees still have to be claimed by whoever
/// controls the token's pump.fun creator fee normally, then voluntarily
/// donated in here. Fully permissionless and trustless in the other
/// direction, though - nobody can be forced to donate, but anyone who does
/// is folding real lamports into the same accumulator every holder's weight
/// draws from, with no way to earmark it for themselves.
pub fn handler(ctx: Context<Donate>, amount: u64) -> Result<()> {
    require!(amount > 0, StackError::ZeroDonation);
    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.mint.key();

    invoke(
        &system_instruction::transfer(
            &ctx.accounts.donor.key(),
            &ctx.accounts.deposit_vault.key(),
            amount,
        ),
        &[
            ctx.accounts.donor.to_account_info(),
            ctx.accounts.deposit_vault.to_account_info(),
            ctx.accounts.system_program.to_account_info(),
        ],
    )?;

    let pool = &mut ctx.accounts.loyalty_pool;
    collect_fee(pool, amount);

    emit!(FeeCollected {
        mint: mint_key,
        amount,
        acc_reward_per_share: pool.acc_reward_per_share,
        total_weighted_shares: pool.total_weighted_shares,
        total_collected: pool.total_collected,
        undistributed: pool.undistributed,
        timestamp: now,
    });

    Ok(())
}

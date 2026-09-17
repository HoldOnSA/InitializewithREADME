use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::*;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct DonateToPool<'info> {
    pub owner: Signer<'info>,

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
        mut,
        has_one = mint,
        seeds = [SEED_POSITION, mint.key().as_ref(), owner.key().as_ref()],
        bump = position.bump
    )]
    pub position: Account<'info, Position>,
}

/// The one tax-exempt destination: the program's own pool address.
///
/// It is exempt precisely because it is not an exit - the tokens are handed to
/// every other holder rather than to the sender. There is nothing to game, and
/// making this the *only* exempt path is what lets `transfer_position` tax
/// unconditionally.
pub fn handler(ctx: Context<DonateToPool>, amount: u64) -> Result<()> {
    require!(amount > 0, StackError::ZeroAmount);

    let now = Clock::get()?.unix_timestamp;
    let owner_key = ctx.accounts.owner.key();
    let mint_key = ctx.accounts.token_config.mint;

    let out = {
        let config = &mut ctx.accounts.token_config;
        let pool = &mut ctx.accounts.loyalty_pool;
        let position = &mut ctx.accounts.position;

        touch_position(position, pool, now);
        // `tax_exempt = true` - the whole amount reaches the pool.
        let out = consume_spendable(position, amount, &config.tax_curve, now, true)?;
        refresh_weight(position, pool, now);
        collect_tax(pool, out.net);

        if position.total_remaining() == 0 {
            config.holder_count = config.holder_count.saturating_sub(1);
        }
        out
    };

    let pool = &ctx.accounts.loyalty_pool;
    emit!(ExitExecuted {
        mint: mint_key,
        owner: owner_key,
        kind: EXIT_KIND_DONATE,
        gross: out.gross,
        tax: 0,
        net: out.net,
        top_tax_bps: 0,
        proceeds_lamports: 0,
        destination: pool.key(),
        timestamp: now,
    });
    emit!(TaxCollected {
        mint: mint_key,
        payer: owner_key,
        amount: out.net,
        acc_reward_per_share: pool.acc_reward_per_share,
        total_weighted_shares: pool.total_weighted_shares,
        pool_total_collected: pool.total_collected,
        undistributed: pool.undistributed,
        timestamp: now,
    });

    Ok(())
}

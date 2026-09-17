use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::*;
use crate::logic::*;
use crate::math::sell_proceeds_lamports;
use crate::state::*;

#[derive(Accounts)]
pub struct Sell<'info> {
    #[account(mut)]
    pub seller: Signer<'info>,

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

    /// Seeds bind this position to `seller`, so no extra owner check is needed.
    #[account(
        mut,
        has_one = mint,
        seeds = [SEED_POSITION, mint.key().as_ref(), seller.key().as_ref()],
        bump = position.bump
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

/// Sell `amount` spendable tokens back to the curve.
///
/// The tax is computed per FIFO lot from that lot's own age, routed into the
/// loyalty pool, and only the post-tax remainder is sold to the curve.
/// `min_proceeds_lamports == 0` disables the slippage check.
pub fn handler(ctx: Context<Sell>, amount: u64, min_proceeds_lamports: u64) -> Result<()> {
    require!(amount > 0, StackError::ZeroAmount);

    let now = Clock::get()?.unix_timestamp;
    let seller_key = ctx.accounts.seller.key();
    let mint_key = ctx.accounts.token_config.mint;

    let (out, proceeds) = {
        let config = &mut ctx.accounts.token_config;
        let pool = &mut ctx.accounts.loyalty_pool;
        let position = &mut ctx.accounts.position;

        touch_position(position, pool, now);

        let out = consume_spendable(position, amount, &config.tax_curve, now, false)?;

        let proceeds = sell_proceeds_lamports(
            config.virtual_sol_reserves,
            config.virtual_token_reserves,
            out.net,
        )
        .ok_or(StackError::MathOverflow)?;
        require!(
            min_proceeds_lamports == 0 || proceeds >= min_proceeds_lamports,
            StackError::SlippageExceeded
        );
        require!(proceeds <= config.real_sol_reserves, StackError::CurveInsolvent);

        // Re-weight before distributing, so a seller cannot collect on their own
        // tax at a weight they no longer hold.
        refresh_weight(position, pool, now);
        collect_tax(pool, out.tax);

        // The taxed portion stays in circulation - the pool owns it now - so
        // only `out.net` is returned to the curve.
        config.virtual_sol_reserves = config
            .virtual_sol_reserves
            .checked_sub(proceeds)
            .ok_or(StackError::MathOverflow)?;
        config.virtual_token_reserves = config
            .virtual_token_reserves
            .checked_add(out.net)
            .ok_or(StackError::MathOverflow)?;
        config.real_sol_reserves = config
            .real_sol_reserves
            .checked_sub(proceeds)
            .ok_or(StackError::MathOverflow)?;
        config.tokens_sold = config
            .tokens_sold
            .checked_sub(out.net)
            .ok_or(StackError::MathOverflow)?;
        config.total_sell_volume_tokens = config.total_sell_volume_tokens.saturating_add(amount);

        if position.total_remaining() == 0 {
            config.holder_count = config.holder_count.saturating_sub(1);
        }

        (out, proceeds)
    };

    // Pay the seller from the curve vault. `real_sol_reserves` was checked
    // above and the vault always holds `rent + real_sol_reserves`, so rent
    // exemption is preserved by construction.
    if proceeds > 0 {
        let vault_info = ctx.accounts.curve_vault.to_account_info();
        let seller_info = ctx.accounts.seller.to_account_info();
        let vault_balance = vault_info.lamports();
        **vault_info.try_borrow_mut_lamports()? = vault_balance
            .checked_sub(proceeds)
            .ok_or(StackError::CurveInsolvent)?;
        let seller_balance = seller_info.lamports();
        **seller_info.try_borrow_mut_lamports()? = seller_balance
            .checked_add(proceeds)
            .ok_or(StackError::MathOverflow)?;
    }

    emit!(ExitExecuted {
        mint: mint_key,
        owner: seller_key,
        kind: EXIT_KIND_SELL,
        gross: out.gross,
        tax: out.tax,
        net: out.net,
        top_tax_bps: out.top_tax_bps,
        proceeds_lamports: proceeds,
        destination: Pubkey::default(),
        timestamp: now,
    });

    if out.tax > 0 {
        let pool = &ctx.accounts.loyalty_pool;
        emit!(TaxCollected {
            mint: mint_key,
            payer: seller_key,
            amount: out.tax,
            acc_reward_per_share: pool.acc_reward_per_share,
            total_weighted_shares: pool.total_weighted_shares,
            pool_total_collected: pool.total_collected,
            undistributed: pool.undistributed,
            timestamp: now,
        });
    }

    Ok(())
}

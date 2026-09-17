use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::*;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct TransferPosition<'info> {
    #[account(mut)]
    pub sender: Signer<'info>,

    /// CHECK: identity/seed for this launch only; never deserialized.
    pub mint: UncheckedAccount<'info>,

    /// CHECK: only used to derive the destination position PDA.
    pub recipient: UncheckedAccount<'info>,

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
        seeds = [SEED_POSITION, mint.key().as_ref(), sender.key().as_ref()],
        bump = from_position.bump
    )]
    pub from_position: Account<'info, Position>,

    #[account(
        init_if_needed,
        payer = sender,
        space = 8 + Position::INIT_SPACE,
        seeds = [SEED_POSITION, mint.key().as_ref(), recipient.key().as_ref()],
        bump
    )]
    pub to_position: Account<'info, Position>,

    pub system_program: Program<'info, System>,
}

/// Wallet-to-wallet transfer.
///
/// This is the anti-gaming rule that matters most: a transfer is not a
/// loophole around the sell tax, it *is* a sell as far as the tax curve is
/// concerned. It runs the identical `consume_spendable` path, at the identical
/// rate, and routes the identical tax into the loyalty pool.
///
/// The recipient's tokens land in a **new lot stamped with the current time**,
/// so tenure does not travel between wallets. Moving a mature position to a
/// fresh wallet therefore buys nothing: you pay the exit tax on the way out and
/// restart the clock on the way in.
pub fn handler(ctx: Context<TransferPosition>, amount: u64) -> Result<()> {
    require!(amount > 0, StackError::ZeroAmount);
    let sender_key = ctx.accounts.sender.key();
    let recipient_key = ctx.accounts.recipient.key();
    require!(sender_key != recipient_key, StackError::SelfTransfer);

    let clock = Clock::get()?;
    let now = clock.unix_timestamp;
    let slot = clock.slot;
    let mint_key = ctx.accounts.token_config.mint;
    let to_bump = ctx.bumps.to_position;

    // Only the program's own pool address is exempt, and that path is
    // `donate_to_pool`, which forfeits the tokens. Nothing reachable here is
    // exempt.
    require!(
        recipient_key != ctx.accounts.token_config.pool_account,
        StackError::SelfTransfer
    );

    let out = {
        let config = &mut ctx.accounts.token_config;
        let pool = &mut ctx.accounts.loyalty_pool;
        let from = &mut ctx.accounts.from_position;

        touch_position(from, pool, now);
        let out = consume_spendable(from, amount, &config.tax_curve, now, false)?;
        refresh_weight(from, pool, now);
        collect_tax(pool, out.tax);

        if from.total_remaining() == 0 {
            config.holder_count = config.holder_count.saturating_sub(1);
        }
        out
    };

    {
        let pool = &mut ctx.accounts.loyalty_pool;
        let to = &mut ctx.accounts.to_position;

        let is_new = to.owner == Pubkey::default();
        if is_new {
            to.owner = recipient_key;
            to.mint = mint_key;
            to.bump = to_bump;
            to.lots = Vec::new();
            to.first_buy_timestamp = now;
        }
        let was_empty = to.total_remaining() == 0;

        touch_position(to, pool, now);
        // Transferred tokens are liquid on arrival but freshly stamped.
        push_liquid_lot(to, out.net, now)?;
        to.total_bought = to
            .total_bought
            .checked_add(out.net)
            .ok_or(StackError::MathOverflow)?;
        // Receiving is a balance increase, so the claim delay restarts.
        to.last_increase_slot = slot;
        refresh_weight(to, pool, now);

        if was_empty {
            ctx.accounts.token_config.holder_count =
                ctx.accounts.token_config.holder_count.saturating_add(1);
        }
    }

    emit!(ExitExecuted {
        mint: mint_key,
        owner: sender_key,
        kind: EXIT_KIND_TRANSFER,
        gross: out.gross,
        tax: out.tax,
        net: out.net,
        top_tax_bps: out.top_tax_bps,
        proceeds_lamports: 0,
        destination: recipient_key,
        timestamp: now,
    });

    if out.tax > 0 {
        let pool = &ctx.accounts.loyalty_pool;
        emit!(TaxCollected {
            mint: mint_key,
            payer: sender_key,
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

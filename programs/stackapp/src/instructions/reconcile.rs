use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::FeeCollected;
use crate::logic::{collect_fee, vault_surplus};
use crate::state::*;

#[derive(Accounts)]
pub struct Reconcile<'info> {
    /// Permissionless: anyone may trigger a sweep. Present only to pay the
    /// transaction fee - mirrors `sync`'s `cranker`.
    pub cranker: Signer<'info>,

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
}

/// Sweep any lamports sitting in `deposit_vault` that neither `donate` nor
/// the registration-marker/rent bookkeeping can explain, and fold them into
/// the pool as real fee revenue - see `logic::vault_surplus` for exactly
/// what counts as "explained".
///
/// This closes a real gap in the donate-only design: a plain SOL transfer
/// straight to `deposit_vault` (the same kind of transfer a registration
/// marker already is) previously just sat there forever, invisible to every
/// holder's reward math, since nothing but `donate` ever called
/// `collect_fee`. Fully permissionless, like `sync` - it can only ever move
/// money that is *already* sitting in the vault into the accumulator; it
/// can't move anything out, and it can't manufacture money that isn't
/// really there.
pub fn handler(ctx: Context<Reconcile>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.mint.key();

    let vault_info = ctx.accounts.deposit_vault.to_account_info();
    let own_rent_floor = Rent::get()?.minimum_balance(vault_info.data_len());
    let actual_lamports = vault_info.lamports();
    let vault = &ctx.accounts.deposit_vault;

    let surplus = vault_surplus(
        actual_lamports,
        own_rent_floor,
        vault.total_marker_deposits,
        vault.total_rent_spent,
        ctx.accounts.loyalty_pool.total_collected,
        ctx.accounts.loyalty_pool.total_claimed,
    );
    require!(surplus > 0, StackError::NothingToReconcile);

    let pool = &mut ctx.accounts.loyalty_pool;
    collect_fee(pool, surplus);

    emit!(FeeCollected {
        mint: mint_key,
        amount: surplus,
        acc_reward_per_share: pool.acc_reward_per_share,
        total_weighted_shares: pool.total_weighted_shares,
        total_collected: pool.total_collected,
        undistributed: pool.undistributed,
        timestamp: now,
    });

    Ok(())
}

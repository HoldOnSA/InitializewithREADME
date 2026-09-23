use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::invoke;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::{FeeCollected, VaultDeficitDetected};
use crate::logic::{collect_fee, vault_expected_balance};
use crate::pumpfun::*;
use crate::state::*;

#[derive(Accounts)]
pub struct PullPumpFee<'info> {
    /// Permissionless: anyone may crank a pull. Present only to pay the
    /// transaction fee - mirrors `sync`/`reconcile`.
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

    /// CHECK: pump.fun's `SharingConfig` PDA for this mint - forwarded into
    /// the CPI, which is what actually reads and validates it. StackApp
    /// never becomes this account's authority; it only ever reads the
    /// address a creator configured on their own via `update_fee_shares_v2`.
    #[account(
        seeds = [SEED_SHARING_CONFIG, mint.key().as_ref()],
        bump,
        seeds::program = PUMP_FEES_PROGRAM_ID,
    )]
    pub sharing_config: UncheckedAccount<'info>,

    /// CHECK: pump.fun's bonding curve PDA for this mint.
    #[account(
        seeds = [SEED_BONDING_CURVE, mint.key().as_ref()],
        bump,
        seeds::program = PUMP_PROGRAM_ID,
    )]
    pub bonding_curve: UncheckedAccount<'info>,

    /// CHECK: pump.fun's creator-fee vault - source of the distribution.
    /// Real seeds are `[b"creator-vault", bonding_curve.creator]` (see
    /// `pumpfun.rs`); derived here off `sharing_config`'s own key, which is
    /// only correct once `mint` is actually migrated into fee-sharing. If it
    /// isn't, this account simply won't match what the CPI'd program itself
    /// independently expects, and the whole call fails safely.
    #[account(
        mut,
        seeds = [SEED_CREATOR_VAULT, sharing_config.key().as_ref()],
        bump,
        seeds::program = PUMP_PROGRAM_ID,
    )]
    pub creator_vault: UncheckedAccount<'info>,

    /// CHECK: pump.fun's event-authority PDA, fixed per program.
    #[account(
        seeds = [SEED_EVENT_AUTHORITY],
        bump,
        seeds::program = PUMP_PROGRAM_ID,
    )]
    pub pump_event_authority: UncheckedAccount<'info>,

    /// CHECK: the pump program itself - one of the accounts its own
    /// `distribute_creator_fees` expects in its account list.
    #[account(address = PUMP_PROGRAM_ID)]
    pub pump_program: UncheckedAccount<'info>,

    pub system_program: Program<'info, System>,
    // `ctx.remaining_accounts`: every current shareholder in `mint`'s
    // `SharingConfig.shareholders`, in that exact order - see
    // `pumpfun::distribute_creator_fees_ix`. Not a fixed struct field
    // because the shareholder count varies per token (up to 10).
}

/// Permissionlessly pull `DepositVault`'s pump.fun creator-fee share (see
/// `pumpfun.rs`) and fold it straight into the accumulator, in the same
/// transaction.
///
/// Two steps, both real:
/// 1. CPI into pump.fun's `distribute_creator_fees`, which pays every
///    shareholder in `mint`'s `SharingConfig` - including `DepositVault`,
///    once a creator has added it - directly out of the creator vault.
/// 2. The same reconcile logic `reconcile.rs` uses, called inline rather
///    than requiring a separate crank. This closes the same class of gap the
///    vault-deficit fix closed for raw transfers: without this, a
///    successful pull would sit as an unaccounted vault balance - invisible
///    to every holder's `sync`/`claim` - until someone happened to call
///    `reconcile` separately, with no bound on how long that could take.
///    `reconcile()`'s only failure mode (`NothingToReconcile`) can only be
///    hit here if the CPI moved zero lamports, which callers are expected to
///    rule out beforehand via pump.fun's own `get_minimum_distributable_fee`
///    - and if it happens anyway, the whole transaction just reverts
///    (Solana transactions are atomic; the CPI's own effects roll back with
///    it), so there is no fund-loss path, only a wasted crank.
///
/// **If `deposit_vault` already has an unresolved deficit** (see
/// `reconcile.rs` / `VaultDeficitDetected` - should never happen under
/// correct operation, but if it does): this instruction will keep reverting
/// with `NothingToReconcile` on every call until a single pull's freshly
/// -received lamports exceed the outstanding deficit in one shot. A pull
/// that only partially covers the deficit produces no net surplus, so the
/// whole transaction - the CPI included - reverts atomically; there is no
/// partial progress. Once a pull finally does exceed the deficit, the call
/// succeeds and fully re-absorbs it - the deficit does not reappear
/// afterward. A cranker retrying naively on failure cannot tell that case
/// apart from the completely routine "no new pump.fun fees have accrued
/// yet" failure, since both raise the same error - **the distinguishing
/// signal is `VaultDeficitDetected`**, emitted only in the deficit case,
/// even though the transaction still reverts (see `subscriber.py`'s
/// allow-list). Any automated cranker built against this instruction should
/// treat an observed `VaultDeficitDetected` as a stop-and-alert condition
/// for that token, not something to retry-loop against: every retry before
/// the underlying condition changes costs a real transaction fee with zero
/// chance of success.
pub fn handler<'info>(ctx: Context<'_, '_, '_, 'info, PullPumpFee<'info>>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.mint.key();
    let deposit_vault_key = ctx.accounts.deposit_vault.key();

    let shareholder_keys: Vec<Pubkey> =
        ctx.remaining_accounts.iter().map(|a| a.key()).collect();

    // Defense in depth, before pump.fun's program is ever invoked:
    // `sharing_config`'s own on-chain data, not the caller's word for it, is
    // what remaining_accounts must match - see `pumpfun.rs`'s doc comment
    // for why this isn't just trusted to pump.fun's internal enforcement.
    assert_matches_on_chain_shareholders(
        &ctx.accounts.sharing_config.try_borrow_data()?,
        &shareholder_keys,
    )?;

    // Even a fully genuine, on-chain-matching list might simply not include
    // StackApp - i.e. this mint's creator never added `deposit_vault` as a
    // shareholder. Cranking pump.fun anyway would accomplish nothing for us
    // while paying everyone else - fail loudly instead of wasting the CPI.
    require!(
        ctx.remaining_accounts
            .iter()
            .any(|a| a.key() == deposit_vault_key),
        StackError::DepositVaultNotAShareholder
    );

    let ix = distribute_creator_fees_ix(
        mint_key,
        ctx.accounts.bonding_curve.key(),
        ctx.accounts.sharing_config.key(),
        ctx.accounts.creator_vault.key(),
        ctx.accounts.pump_event_authority.key(),
        &shareholder_keys,
    );

    let mut account_infos = vec![
        ctx.accounts.mint.to_account_info(),
        ctx.accounts.bonding_curve.to_account_info(),
        ctx.accounts.sharing_config.to_account_info(),
        ctx.accounts.creator_vault.to_account_info(),
        ctx.accounts.system_program.to_account_info(),
        ctx.accounts.pump_event_authority.to_account_info(),
        ctx.accounts.pump_program.to_account_info(),
    ];
    account_infos.extend(ctx.remaining_accounts.iter().cloned());

    invoke(&ix, &account_infos)?;

    // Inline reconcile - identical math to `reconcile.rs`, run as this same
    // instruction's tail rather than a separate crank. See the doc comment
    // above for why.
    let vault_info = ctx.accounts.deposit_vault.to_account_info();
    let own_rent_floor = Rent::get()?.minimum_balance(vault_info.data_len());
    let actual_lamports = vault_info.lamports();
    let vault = &ctx.accounts.deposit_vault;
    let expected = vault_expected_balance(
        own_rent_floor,
        vault.total_marker_deposits,
        vault.total_rent_spent,
        ctx.accounts.loyalty_pool.total_collected,
        ctx.accounts.loyalty_pool.total_claimed,
    );

    if actual_lamports < expected {
        emit!(VaultDeficitDetected {
            mint: mint_key,
            deposit_vault: deposit_vault_key,
            actual_lamports,
            expected_lamports: expected,
            deficit: expected - actual_lamports,
            timestamp: now,
        });
    }

    let surplus = actual_lamports.saturating_sub(expected);
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

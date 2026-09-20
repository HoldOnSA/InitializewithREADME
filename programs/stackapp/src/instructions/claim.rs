use anchor_lang::prelude::*;
use anchor_spl::token::TokenAccount;

use crate::ata::derive_ata;
use crate::constants::*;
use crate::errors::StackError;
use crate::events::{FeeCollected, RewardClaimed};
use crate::logic::*;
use crate::pumpfun::pull_pump_fee;
use crate::state::*;

#[derive(Accounts)]
pub struct Claim<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    /// CHECK: identity/seed for this token only.
    pub mint: UncheckedAccount<'info>,

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
        has_one = owner,
        seeds = [SEED_REGISTRATION, mint.key().as_ref(), owner.key().as_ref()],
        bump = registration.bump
    )]
    pub registration: Account<'info, Registration>,

    #[account(
        mut,
        has_one = mint,
        seeds = [SEED_DEPOSIT_VAULT, mint.key().as_ref()],
        bump = token_config.deposit_bump
    )]
    pub deposit_vault: Account<'info, DepositVault>,

    /// The caller's real SPL balance for this mint - verified in the handler
    /// (see `ata::derive_ata`) to be their own canonical ATA. See `sync`'s
    /// docs on why this, not any cached number, is what determines weight.
    pub holder_token_account: Account<'info, TokenAccount>,
}

/// Pull in whatever pump.fun has made newly available, then pay the caller
/// their accumulator share - the "no bot needed" design: calling this to
/// collect your own share is what also settles everyone else's.
///
/// 1. **Pull.** `pull_pump_fee` is currently a stub (see `pumpfun.rs` and
///    `SECURITY_NOTES.md`) - real integration is a fast-follow once verified
///    against a live registered token. Whatever it returns, real or zero,
///    folds into the accumulator the same way.
/// 2. **Sync.** Settle at the OLD weight, then re-price from the caller's
///    *live* token balance - identical to what the permissionless `sync`
///    instruction does, inlined here so a claim never pays out against a
///    stale weight.
/// 3. **Pay.** Real lamports, straight out of `deposit_vault`'s own balance,
///    gated by `MIN_CLAIM_DELAY_SLOTS` since this registration's weight last
///    increased - the flash-loan guard: buy in, watch a fee land, and claim
///    in the same block does not work.
pub fn handler(ctx: Context<Claim>) -> Result<()> {
    let clock = Clock::get()?;
    let now = clock.unix_timestamp;
    let slot = clock.slot;
    let mint_key = ctx.accounts.token_config.mint;
    let owner_key = ctx.accounts.owner.key();

    require_keys_eq!(
        ctx.accounts.holder_token_account.key(),
        derive_ata(&owner_key, &mint_key),
        StackError::NotHoldersAta
    );
    require!(
        claim_is_eligible(&ctx.accounts.registration, slot),
        StackError::ClaimTooSoon
    );

    let pulled = {
        let deposit_info = ctx.accounts.deposit_vault.to_account_info();
        pull_pump_fee(&deposit_info, &mint_key)?
    };
    let pool = &mut ctx.accounts.loyalty_pool;
    collect_fee(pool, pulled);
    if pulled > 0 {
        emit!(FeeCollected {
            mint: mint_key,
            amount: pulled,
            acc_reward_per_share: pool.acc_reward_per_share,
            total_weighted_shares: pool.total_weighted_shares,
            total_collected: pool.total_collected,
            undistributed: pool.undistributed,
            timestamp: now,
        });
    }

    let registration = &mut ctx.accounts.registration;
    let balance = ctx.accounts.holder_token_account.amount;
    touch_registration(registration, pool);
    let previous_weight = registration.weighted_shares;
    refresh_weight(registration, pool, balance, now);
    if registration.weighted_shares > previous_weight {
        registration.last_sync_slot = slot;
    }

    let payout = registration.pending_rewards;
    require!(payout > 0, StackError::NothingToClaim);
    registration.pending_rewards = 0;
    registration.lifetime_rewards_claimed = registration
        .lifetime_rewards_claimed
        .checked_add(payout)
        .ok_or(StackError::MathOverflow)?;
    pool.total_claimed = pool.total_claimed.saturating_add(payout);
    let weighted_shares = registration.weighted_shares;
    let lifetime_claimed = registration.lifetime_rewards_claimed;

    let vault_info = ctx.accounts.deposit_vault.to_account_info();
    let owner_info = ctx.accounts.owner.to_account_info();
    let vault_balance = vault_info.lamports();
    **vault_info.try_borrow_mut_lamports()? = vault_balance
        .checked_sub(payout)
        .ok_or(StackError::MathOverflow)?;
    let owner_balance = owner_info.lamports();
    **owner_info.try_borrow_mut_lamports()? = owner_balance
        .checked_add(payout)
        .ok_or(StackError::MathOverflow)?;

    emit!(RewardClaimed {
        mint: mint_key,
        owner: owner_key,
        amount: payout,
        weighted_shares,
        lifetime_claimed,
        timestamp: now,
    });

    Ok(())
}

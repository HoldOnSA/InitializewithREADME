use anchor_lang::prelude::*;

use crate::ata::{derive_ata, is_supported_token_program, read_token_amount};
use crate::constants::*;
use crate::errors::StackError;
use crate::events::RewardClaimed;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct Claim<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    /// CHECK: identity/seed for this token only; also read directly for its
    /// owning program, to pick the right token program (legacy vs
    /// Token-2022) - never deserialized as mint data.
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

    /// CHECK: the caller's real token account for this mint - verified in
    /// the handler (see `ata::derive_ata`) to be their own canonical ATA
    /// under whichever token program actually owns `mint`. See `sync`'s docs
    /// on why this, not any cached number, is what determines weight, and
    /// on why this isn't typed as `Account<TokenAccount>`.
    pub holder_token_account: UncheckedAccount<'info>,
}

/// Sync the caller's own weight against their live balance, then pay out
/// their accumulator share - the "no bot needed" design: calling this to
/// collect your own share is what also settles everyone else's.
///
/// `LoyaltyPool` is only ever fed by the permissionless `donate` instruction
/// (see `instructions/donate.rs`) - there is no automatic pull from
/// pump.fun here. See `SECURITY_NOTES.md` for why: pump.fun's own
/// multi-wallet fee-sharing routes all fees into a single vault keyed to the
/// `SharingConfig` PDA, not to individual shareholder wallets, and pulling a
/// shareholder's own cut back out requires the config's `authority` to sign
/// - not something StackApp can do permissionlessly for a token it doesn't
/// control. `donate` sidesteps that entirely: anyone, especially the
/// creator after claiming their own pump.fun fee normally, can voluntarily
/// route SOL into this pool with no special authority needed.
///
/// 1. **Sync.** Settle at the OLD weight, then re-price from the caller's
///    *live* token balance - identical to what the permissionless `sync`
///    instruction does, inlined here so a claim never pays out against a
///    stale weight.
/// 2. **Pay.** Real lamports, straight out of `deposit_vault`'s own balance,
///    gated by `MIN_CLAIM_DELAY_SLOTS` since this registration's weight last
///    increased - the flash-loan guard: buy in, watch a fee land, and claim
///    in the same block does not work.
pub fn handler(ctx: Context<Claim>) -> Result<()> {
    let clock = Clock::get()?;
    let now = clock.unix_timestamp;
    let slot = clock.slot;
    let mint_key = ctx.accounts.token_config.mint;
    let owner_key = ctx.accounts.owner.key();

    let token_program = *ctx.accounts.mint.to_account_info().owner;
    require!(
        is_supported_token_program(&token_program),
        StackError::UnsupportedTokenProgram
    );
    require_keys_eq!(
        ctx.accounts.holder_token_account.key(),
        derive_ata(&owner_key, &mint_key, &token_program),
        StackError::NotHoldersAta
    );
    require!(
        claim_is_eligible(&ctx.accounts.registration, slot),
        StackError::ClaimTooSoon
    );

    let balance = read_token_amount(&ctx.accounts.holder_token_account.to_account_info())?;
    let pool = &mut ctx.accounts.loyalty_pool;
    let registration = &mut ctx.accounts.registration;
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

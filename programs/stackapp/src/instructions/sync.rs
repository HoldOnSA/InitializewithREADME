use anchor_lang::prelude::*;

use crate::ata::{derive_ata, is_supported_token_program, read_token_amount};
use crate::constants::*;
use crate::errors::StackError;
use crate::events::WeightSynced;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct Sync<'info> {
    /// Permissionless. Anyone may refresh anyone's weight.
    pub cranker: Signer<'info>,

    /// CHECK: identity/seed for this token only; also read directly for its
    /// owning program, to pick the right token program (legacy vs
    /// Token-2022) - never deserialized as mint data.
    pub mint: UncheckedAccount<'info>,

    /// CHECK: identity/seed only, whichever wallet's weight is being synced.
    pub owner: UncheckedAccount<'info>,

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

    /// CHECK: the owner's real token account for this mint - verified in the
    /// handler (see `ata::derive_ata`) to genuinely be `owner`'s own
    /// canonical ATA under whichever token program actually owns `mint`
    /// (legacy SPL Token or Token-2022), so nobody can substitute a bigger
    /// balance. Not typed as `Account<TokenAccount>`: that type only
    /// deserializes the legacy layout at an exact 165-byte length, which
    /// fails on real Token-2022 accounts carrying extensions (e.g. every ATA
    /// the current ATA program creates, which always adds `ImmutableOwner`).
    /// Balance is read manually instead (`ata::read_token_amount`).
    pub holder_token_account: UncheckedAccount<'info>,
}

/// Re-price a registration's weight from `owner`'s live token balance.
///
/// Weight only changes in storage when a registration is touched, which is
/// what keeps `LoyaltyPool::total_weighted_shares` consistent without the
/// program ever iterating over holders - this permissionless crank exists so
/// a wallet that never calls `claim` still gets its tenure tier applied.
/// Settling before re-weighting means the crank can never move rewards that
/// were already earned at the old weight.
pub fn handler(ctx: Context<Sync>) -> Result<()> {
    let clock = Clock::get()?;
    let now = clock.unix_timestamp;
    let slot = clock.slot;
    let mint_key = ctx.accounts.token_config.mint;
    let owner_key = ctx.accounts.registration.owner;

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
    let balance = read_token_amount(&ctx.accounts.holder_token_account.to_account_info())?;

    let pool = &mut ctx.accounts.loyalty_pool;
    let registration = &mut ctx.accounts.registration;

    let previous_weight = registration.weighted_shares;
    touch_registration(registration, pool);
    refresh_weight(registration, pool, balance, now);
    if registration.weighted_shares > previous_weight {
        registration.last_sync_slot = slot;
    }

    emit!(WeightSynced {
        mint: mint_key,
        owner: owner_key,
        balance,
        previous_weight,
        new_weight: registration.weighted_shares,
        total_weighted_shares: pool.total_weighted_shares,
        timestamp: now,
    });

    Ok(())
}

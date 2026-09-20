use anchor_lang::prelude::*;
use anchor_spl::token::TokenAccount;

use crate::ata::derive_ata;
use crate::constants::*;
use crate::errors::StackError;
use crate::events::WeightSynced;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct Sync<'info> {
    /// Permissionless. Anyone may refresh anyone's weight.
    pub cranker: Signer<'info>,

    /// CHECK: identity/seed for this token only.
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

    /// The owner's real SPL balance for this mint - the only source of
    /// truth for weight. Verified in the handler (see `ata::derive_ata`) to
    /// genuinely be `owner`'s own canonical ATA for this exact mint, so
    /// nobody can substitute a bigger balance.
    pub holder_token_account: Account<'info, TokenAccount>,
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

    require_keys_eq!(
        ctx.accounts.holder_token_account.key(),
        derive_ata(&owner_key, &mint_key),
        StackError::NotHoldersAta
    );
    let balance = ctx.accounts.holder_token_account.amount;

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

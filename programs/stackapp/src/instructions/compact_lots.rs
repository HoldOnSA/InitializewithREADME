use anchor_lang::prelude::*;

use crate::constants::*;
use crate::events::LotsCompacted;
use crate::logic::*;
use crate::state::*;

#[derive(Accounts)]
pub struct CompactLots<'info> {
    pub owner: Signer<'info>,

    /// CHECK: identity/seed for this launch only; never deserialized.
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
        seeds = [SEED_POSITION, mint.key().as_ref(), owner.key().as_ref()],
        bump = position.bump
    )]
    pub position: Account<'info, Position>,
}

/// Merge the two oldest lots so a position at `MAX_LOTS` can keep buying.
///
/// Owner-initiated only, and the merged lot takes the **newer** of the two
/// timestamps. Compaction is therefore always a cost to the owner (higher tax,
/// lower tenure multiplier) and can never be used to launder fresh tokens into
/// an older lot's rate.
pub fn handler(ctx: Context<CompactLots>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.token_config.mint;
    let owner_key = ctx.accounts.owner.key();

    let pool = &mut ctx.accounts.loyalty_pool;
    let position = &mut ctx.accounts.position;

    touch_position(position, pool, now);
    compact_oldest_lots(position)?;
    prune_empty_lots(position);
    refresh_weight(position, pool, now);

    emit!(LotsCompacted {
        mint: mint_key,
        owner: owner_key,
        lots_remaining: position.lots.len() as u8,
        merged_timestamp: position.lots.first().map(|l| l.buy_timestamp).unwrap_or(now),
    });

    Ok(())
}

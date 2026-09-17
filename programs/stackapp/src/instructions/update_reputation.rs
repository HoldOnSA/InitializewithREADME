use anchor_lang::prelude::*;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::{ReputationUpdated, TierUp};
use crate::logic::*;
use crate::math::{reputation_score_delta, tier_for_score};
use crate::state::*;

#[derive(Accounts)]
pub struct UpdateReputation<'info> {
    #[account(mut)]
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

    /// Platform-wide and non-transferable: the PDA is derived from `owner`,
    /// there is no authority field, and no instruction reassigns or closes it.
    #[account(
        init_if_needed,
        payer = owner,
        space = 8 + Reputation::INIT_SPACE,
        seeds = [SEED_REPUTATION, owner.key().as_ref()],
        bump
    )]
    pub reputation: Account<'info, Reputation>,

    pub system_program: Program<'info, System>,
}

/// Credit a wallet's platform-wide reputation for holding a position past its
/// vest duration without exiting.
///
/// The score delta is `capital_at_risk_lamports * window_seconds`, which is
/// **linear in capital and counts no wallets at all**. That is the whole
/// anti-sybil argument: a concave function would pay `n * f(C/n) > f(C)` and
/// reward sharding; linear pays the same either way, and because the division
/// floors, sharding is weakly worse. Tier thresholds are then applied to a
/// single wallet's score, so sharding strictly loses tier.
pub fn handler(ctx: Context<UpdateReputation>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let owner_key = ctx.accounts.owner.key();
    let mint_key = ctx.accounts.token_config.mint;
    let vest = ctx.accounts.token_config.vest_duration_seconds;
    let reputation_bump = ctx.bumps.reputation;

    // Keep weight fresh so the passport and the pool agree on this position.
    {
        let pool = &mut ctx.accounts.loyalty_pool;
        let position = &mut ctx.accounts.position;
        touch_position(position, pool, now);
        refresh_weight(position, pool, now);
    }

    let position = &mut ctx.accounts.position;
    require!(position.total_bought > 0, StackError::NotMatured);
    require!(
        retained_through_maturity(position),
        StackError::ExitedBeforeMaturity
    );

    // The credit window runs from the later of "first buy" and "last credited",
    // so each maturity period is paid once and only once.
    let window_start = position
        .last_reputation_timestamp
        .max(position.first_buy_timestamp);
    let window = now - window_start;
    require!(window >= 0, StackError::NotMatured);
    // A zero vest duration still requires a real holding period before it counts.
    let required = if vest > 0 { vest } else { 86_400 };
    require!(window >= required, StackError::NotMatured);

    let capital = position.cost_basis_lamports;
    let score_delta = reputation_score_delta(capital, window);
    let volume_delta = position
        .tenure_weighted_volume
        .saturating_sub(position.credited_volume);
    require!(
        score_delta > 0 || volume_delta > 0,
        StackError::ReputationAlreadyCredited
    );

    position.credited_volume = position.tenure_weighted_volume;
    position.last_reputation_timestamp = now;
    let first_credit = position.maturity_credits == 0;
    position.maturity_credits = position.maturity_credits.saturating_add(1);

    let reputation = &mut ctx.accounts.reputation;
    if reputation.owner == Pubkey::default() {
        reputation.owner = owner_key;
        reputation.bump = reputation_bump;
        reputation.first_seen_timestamp = now;
    }
    let previous_tier = reputation.tier;
    reputation.score = reputation.score.saturating_add(score_delta);
    reputation.total_tenure_weighted_volume = reputation
        .total_tenure_weighted_volume
        .saturating_add(volume_delta);
    if first_credit {
        reputation.tokens_held_to_maturity =
            reputation.tokens_held_to_maturity.saturating_add(1);
    }
    reputation.tier = tier_for_score(reputation.score);
    reputation.last_update_timestamp = now;

    emit!(ReputationUpdated {
        owner: owner_key,
        mint: mint_key,
        score_delta,
        score: reputation.score,
        tier: reputation.tier,
        tokens_held_to_maturity: reputation.tokens_held_to_maturity,
        total_tenure_weighted_volume: reputation.total_tenure_weighted_volume,
        capital_at_risk_lamports: capital,
        window_seconds: window,
        timestamp: now,
    });

    if reputation.tier > previous_tier {
        emit!(TierUp {
            owner: owner_key,
            previous_tier,
            new_tier: reputation.tier,
            score: reputation.score,
            timestamp: now,
        });
    }

    Ok(())
}

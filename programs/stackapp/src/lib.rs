//! # StackApp - tenure-weighted memecoin launchpad
//!
//! **Devnet prototype. Not audited. Do not deploy to mainnet.** See README.
//!
//! The premise: a launchpad where holding longer is mechanically rewarded and
//! leaving early mechanically funds the people who stayed.
//!
//! * Every position is a set of **FIFO lots**, each stamped with its own buy
//!   time. Averaging the buy timestamp would let fresh capital hide behind an
//!   old position's tenure; lots do not.
//! * **Every** balance decrease - a sell *or* a wallet-to-wallet transfer -
//!   runs the same taxed exit path. The only exempt destination is the
//!   program's own pool, and that path gives the tokens away.
//! * The exit tax funds a **loyalty pool** distributed through an O(1)
//!   reward-per-share accumulator. The program never loops over holders.
//! * Weight is `capital x tenure_multiplier(age)` and reputation is
//!   `capital x seconds_held`. Both are linear in capital and count no wallets,
//!   so sharding a position across many wallets never out-earns holding it in
//!   one.

use anchor_lang::prelude::*;

pub mod constants;
pub mod errors;
pub mod events;
pub mod instructions;
pub mod logic;
pub mod math;
pub mod state;

use instructions::*;
use state::TaxPoint;

declare_id!("GGqhbbXPZg2GGZP1o7CUeNkqATybrDJbX7cxJiwEzaFL");

#[program]
pub mod stackapp {
    use super::*;

    /// Create the `TokenConfig`, `LoyaltyPool` and curve vault for a new mint.
    ///
    /// Pass `0` for either reserve to take the prototype default.
    pub fn initialize_launch(
        ctx: Context<InitializeLaunch>,
        tax_curve: Vec<TaxPoint>,
        vest_duration_seconds: i64,
        virtual_sol_reserves: u64,
        virtual_token_reserves: u64,
        decimals: u8,
    ) -> Result<()> {
        instructions::initialize_launch::handler(
            ctx,
            tax_curve,
            vest_duration_seconds,
            virtual_sol_reserves,
            virtual_token_reserves,
            decimals,
        )
    }

    /// Buy `amount` tokens off the bonding curve into a new FIFO lot.
    pub fn buy(ctx: Context<Buy>, amount: u64, max_cost_lamports: u64) -> Result<()> {
        instructions::buy::handler(ctx, amount, max_cost_lamports)
    }

    /// Move linearly-unlocked tokens into the spendable balance.
    pub fn claim_vested(ctx: Context<ClaimVested>) -> Result<()> {
        instructions::claim_vested::handler(ctx)
    }

    /// Sell spendable tokens back to the curve, taxed per FIFO lot age.
    pub fn sell(ctx: Context<Sell>, amount: u64, min_proceeds_lamports: u64) -> Result<()> {
        instructions::sell::handler(ctx, amount, min_proceeds_lamports)
    }

    /// Wallet-to-wallet transfer. Taxed exactly like a sell; tenure does not
    /// travel with the tokens.
    pub fn transfer_position(ctx: Context<TransferPosition>, amount: u64) -> Result<()> {
        instructions::transfer_position::handler(ctx, amount)
    }

    /// The one tax-exempt destination: the program's own pool.
    pub fn donate_to_pool(ctx: Context<DonateToPool>, amount: u64) -> Result<()> {
        instructions::donate_to_pool::handler(ctx, amount)
    }

    /// Pay out this position's accrued share of the loyalty pool.
    pub fn claim_pool_share(ctx: Context<ClaimPoolShare>) -> Result<()> {
        instructions::claim_pool_share::handler(ctx)
    }

    /// Credit platform-wide reputation for a position held past maturity.
    pub fn update_reputation(ctx: Context<UpdateReputation>) -> Result<()> {
        instructions::update_reputation::handler(ctx)
    }

    /// Permissionless crank re-pricing a stale position's tenure weight.
    pub fn sync_weight(ctx: Context<SyncWeight>) -> Result<()> {
        instructions::sync_weight::handler(ctx)
    }

    /// Merge the two oldest lots so a full position can keep buying.
    pub fn compact_lots(ctx: Context<CompactLots>) -> Result<()> {
        instructions::compact_lots::handler(ctx)
    }
}

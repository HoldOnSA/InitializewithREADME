use anchor_lang::prelude::*;

pub mod ata;
pub mod constants;
pub mod errors;
pub mod events;
pub mod instructions;
pub mod logic;
pub mod math;
pub mod state;

use instructions::*;

// A genuinely new program identity - different accounts, different
// instructions - deployed fresh to devnet rather than upgrading the old
// independent-protocol program in place. The account shapes changed too much
// to share a program ID safely: Anchor discriminators are derived from a
// struct's *name* alone, and this design reuses names like `TokenConfig` and
// `LoyaltyPool` for structs with entirely different fields, so an in-place
// upgrade risked the new code mis-reading a leftover old-shape account at a
// colliding PDA.
declare_id!("BBAbsh9UHVt7xiqeuVo23R4MPbNzXXNp2gsLwu6jAa1M");

#[program]
pub mod stackapp {
    use super::*;

    /// One-time setup: sets the indexer backend's authority key.
    pub fn initialize_config(ctx: Context<InitializeConfig>, authority: Pubkey) -> Result<()> {
        instructions::initialize_config::handler(ctx, authority)
    }

    /// Rotate the indexer backend's key. Current-authority-gated.
    pub fn update_authority(ctx: Context<UpdateAuthority>, new_authority: Pubkey) -> Result<()> {
        instructions::update_authority::handler(ctx, new_authority)
    }

    /// Start tracking a pump.fun mint. Authority-gated.
    pub fn register_mint(ctx: Context<RegisterMint>, creator: Pubkey) -> Result<()> {
        instructions::register_mint::handler(ctx, creator)
    }

    /// Write a wallet's registration after the indexer observes its
    /// marker-SOL transfer. Authority-gated.
    pub fn write_registration(ctx: Context<WriteRegistration>) -> Result<()> {
        instructions::write_registration::handler(ctx)
    }

    /// Permissionless: re-price a registration's weight from its owner's
    /// live token balance.
    pub fn sync(ctx: Context<Sync>) -> Result<()> {
        instructions::sync::handler(ctx)
    }

    /// Sync the caller's own weight, then pay out their accumulator share.
    pub fn claim(ctx: Context<Claim>) -> Result<()> {
        instructions::claim::handler(ctx)
    }

    /// Permissionless: voluntarily route lamports into a token's pool. The
    /// only source of real fee revenue - see `SECURITY_NOTES.md`.
    pub fn donate(ctx: Context<Donate>, amount: u64) -> Result<()> {
        instructions::donate::handler(ctx, amount)
    }
}

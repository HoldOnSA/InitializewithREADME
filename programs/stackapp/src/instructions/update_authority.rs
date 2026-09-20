use anchor_lang::prelude::*;

use crate::constants::*;
use crate::state::*;

#[derive(Accounts)]
pub struct UpdateAuthority<'info> {
    pub authority: Signer<'info>,

    #[account(
        mut,
        has_one = authority,
        seeds = [SEED_GLOBAL_CONFIG],
        bump = global_config.bump
    )]
    pub global_config: Account<'info, GlobalConfig>,
}

/// Rotate the indexer backend's key. Only the CURRENT authority can do this.
pub fn handler(ctx: Context<UpdateAuthority>, new_authority: Pubkey) -> Result<()> {
    ctx.accounts.global_config.authority = new_authority;
    Ok(())
}

use anchor_lang::prelude::*;

use crate::constants::*;
use crate::state::*;

#[derive(Accounts)]
pub struct InitializeConfig<'info> {
    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        init,
        payer = payer,
        space = 8 + GlobalConfig::INIT_SPACE,
        seeds = [SEED_GLOBAL_CONFIG],
        bump
    )]
    pub global_config: Account<'info, GlobalConfig>,

    pub system_program: Program<'info, System>,
}

/// One-time setup. `init` on `global_config` means this can only ever
/// succeed once; a second call fails outright rather than silently
/// overwriting the authority.
pub fn handler(ctx: Context<InitializeConfig>, authority: Pubkey) -> Result<()> {
    let config = &mut ctx.accounts.global_config;
    config.authority = authority;
    config.bump = ctx.bumps.global_config;
    Ok(())
}

use anchor_lang::prelude::*;
use anchor_lang::solana_program::program::invoke_signed;
use anchor_lang::solana_program::system_instruction;

use crate::constants::*;
use crate::errors::StackError;
use crate::events::WalletRegistered;
use crate::state::*;

#[derive(Accounts)]
pub struct WriteRegistration<'info> {
    pub authority: Signer<'info>,

    #[account(
        seeds = [SEED_GLOBAL_CONFIG],
        bump = global_config.bump,
        has_one = authority
    )]
    pub global_config: Account<'info, GlobalConfig>,

    /// CHECK: identity/seed for the wallet being registered; never signs -
    /// this instruction only ever runs because the indexer already saw a
    /// real marker transfer FROM this wallet.
    pub owner: UncheckedAccount<'info>,

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
        seeds = [SEED_DEPOSIT_VAULT, mint.key().as_ref()],
        bump = token_config.deposit_bump
    )]
    pub deposit_vault: Account<'info, DepositVault>,

    /// CHECK: created manually in the handler, funded from `deposit_vault`.
    /// Anchor's `#[account(init, payer = ...)]` sugar requires `payer` to be
    /// a wallet `Signer`, which a program PDA can never be - `deposit_vault`
    /// instead authorizes the transfer itself via `invoke_signed` on its own
    /// seeds. Constrained here only for its address (seeds/bump), not
    /// deserialized, since it doesn't exist yet when this runs.
    #[account(
        mut,
        seeds = [SEED_REGISTRATION, mint.key().as_ref(), owner.key().as_ref()],
        bump
    )]
    pub registration: UncheckedAccount<'info>,

    pub system_program: Program<'info, System>,
}

/// Write a `Registration` for (owner, mint) after the indexer has observed
/// `owner` send `REGISTRATION_MARKER_LAMPORTS` to `deposit_vault`.
///
/// Authority-gated: there is no on-chain proof the marker transfer actually
/// happened, only the indexer's word for it. See `SECURITY_NOTES.md` for
/// exactly what that trust does and does not cover - notably, it can start
/// someone's tenure clock, but it can never inflate their actual payout,
/// which `sync`/`claim` compute from a live balance read every time.
pub fn handler(ctx: Context<WriteRegistration>) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let mint_key = ctx.accounts.mint.key();
    let owner_key = ctx.accounts.owner.key();

    let space = 8 + Registration::INIT_SPACE;
    let rent = Rent::get()?.minimum_balance(space);

    let deposit_bump = ctx.accounts.token_config.deposit_bump;
    let deposit_seeds: &[&[u8]] = &[SEED_DEPOSIT_VAULT, mint_key.as_ref(), &[deposit_bump]];
    let registration_bump = ctx.bumps.registration;
    let registration_seeds: &[&[u8]] =
        &[SEED_REGISTRATION, mint_key.as_ref(), owner_key.as_ref(), &[registration_bump]];

    // `deposit_vault` authorizes this transfer of its own lamports the same
    // way any program-owned PDA does: `invoke_signed` with its own seeds
    // stands in for a signature, since only this program could have
    // produced them.
    invoke_signed(
        &system_instruction::create_account(
            &ctx.accounts.deposit_vault.key(),
            &ctx.accounts.registration.key(),
            rent,
            space as u64,
            &crate::ID,
        ),
        &[
            ctx.accounts.deposit_vault.to_account_info(),
            ctx.accounts.registration.to_account_info(),
            ctx.accounts.system_program.to_account_info(),
        ],
        &[deposit_seeds, registration_seeds],
    )?;

    // A marker transfer is invisible on chain - this call running at all is
    // the only evidence one happened (see the honor-system note above). This
    // is what lets `reconcile` tell "marker money not yet consumed by rent"
    // apart from real fee revenue, without needing to observe the transfer
    // itself.
    let vault = &mut ctx.accounts.deposit_vault;
    vault.total_marker_deposits = vault
        .total_marker_deposits
        .checked_add(REGISTRATION_MARKER_LAMPORTS)
        .ok_or(StackError::MathOverflow)?;
    vault.total_rent_spent = vault
        .total_rent_spent
        .checked_add(rent)
        .ok_or(StackError::MathOverflow)?;

    let registration = Registration {
        owner: owner_key,
        mint: mint_key,
        registered_at: now,
        weighted_shares: 0,
        reward_checkpoint: 0,
        pending_rewards: 0,
        lifetime_rewards_claimed: 0,
        last_sync_slot: 0,
        bump: registration_bump,
    };
    {
        let mut data = ctx.accounts.registration.try_borrow_mut_data()?;
        let mut writer: &mut [u8] = &mut data;
        registration.try_serialize(&mut writer)?;
    }

    emit!(WalletRegistered {
        mint: mint_key,
        owner: owner_key,
        registered_at: now,
    });

    Ok(())
}

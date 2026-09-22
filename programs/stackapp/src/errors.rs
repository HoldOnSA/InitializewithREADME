use anchor_lang::prelude::*;

#[error_code]
pub enum StackError {
    #[msg("Arithmetic overflow")]
    MathOverflow,
    #[msg("Claim is not yet eligible: minimum block delay after a weight increase has not elapsed")]
    ClaimTooSoon,
    #[msg("Nothing to claim")]
    NothingToClaim,
    #[msg("Token account is not the owner's canonical associated token account for this mint")]
    NotHoldersAta,
    #[msg("Mint is not owned by a supported token program")]
    UnsupportedTokenProgram,
    #[msg("Token account data is missing or uninitialized")]
    InvalidTokenAccount,
    #[msg("Donation amount must be greater than zero")]
    ZeroDonation,
    #[msg("The deposit vault's balance is already fully accounted for - nothing to sweep")]
    NothingToReconcile,
}

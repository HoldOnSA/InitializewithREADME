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
}

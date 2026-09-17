use anchor_lang::prelude::*;

/// Lamport vault backing a mint's bonding curve. PDA: `["curve_vault", mint]`.
///
/// Program-owned (not a `SystemAccount`) so the program can debit it directly
/// when paying out a sale. Its lamport balance is always
/// `rent_exempt_minimum + TokenConfig::real_sol_reserves`.
#[account]
#[derive(InitSpace, Debug)]
pub struct CurveVault {
    pub mint: Pubkey,
    pub bump: u8,
}

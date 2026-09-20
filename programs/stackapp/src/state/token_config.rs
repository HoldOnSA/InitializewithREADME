use anchor_lang::prelude::*;

/// Per-mint registration record. PDA: `["config", mint]`.
///
/// Deliberately thin: StackApp doesn't run the trade, the curve, or the tax
/// for this token, so there's no tax curve, no reserves, no decimals to
/// track here - just enough to identify the mint, who opted it in, and
/// where its fee pull lands.
#[account]
#[derive(InitSpace, Debug)]
pub struct TokenConfig {
    pub mint: Pubkey,
    /// Informational: the pump.fun creator who opted this mint in. Not an
    /// authority - nothing here is gated on this field.
    pub creator: Pubkey,
    pub registered_at: i64,
    pub deposit_vault: Pubkey,
    pub deposit_bump: u8,
    pub bump: u8,
}

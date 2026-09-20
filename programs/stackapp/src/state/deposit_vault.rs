use anchor_lang::prelude::*;

/// A single token's unique fee-deposit and registration address. PDA:
/// `["deposit", mint]`.
///
/// One per token, not shared - a wallet holding several tracked tokens
/// registers for each independently, with no ambiguity about which marker
/// transfer was for which token. Program-owned and program-signed (via
/// `invoke_signed` on these same seeds), the same pattern the old design
/// used for `CurveVault`: it holds real lamports directly as its own
/// account balance, both the registration markers and (once wired) whatever
/// `collect_creator_fee` pays out.
#[account]
#[derive(InitSpace, Debug)]
pub struct DepositVault {
    pub mint: Pubkey,
    pub bump: u8,
}

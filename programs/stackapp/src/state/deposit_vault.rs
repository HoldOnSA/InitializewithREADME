use anchor_lang::prelude::*;

/// A single token's unique fee-deposit and registration address. PDA:
/// `["deposit", mint]`.
///
/// One per token, not shared - a wallet holding several tracked tokens
/// registers for each independently, with no ambiguity about which marker
/// transfer was for which token. Program-owned and program-signed (via
/// `invoke_signed` on these same seeds), the same pattern the old design
/// used for `CurveVault`: it holds real lamports directly as its own
/// account balance - registration markers, whatever `donate` voluntarily
/// routes in, and (see `reconcile`) anything else that lands here as a
/// plain SOL transfer outside either of those.
#[account]
#[derive(InitSpace, Debug)]
pub struct DepositVault {
    pub mint: Pubkey,
    /// Sum of `REGISTRATION_MARKER_LAMPORTS` over every `write_registration`
    /// ever called for this mint - a proxy for "markers received", since a
    /// marker transfer itself is invisible on chain (see `SECURITY_NOTES.md`).
    /// Together with `total_rent_spent`, this lets `reconcile` tell "marker
    /// money not yet consumed by rent" apart from real fee revenue.
    pub total_marker_deposits: u64,
    /// Sum of the rent-exemption `write_registration` has ever paid out of
    /// this vault to create `Registration` PDAs.
    pub total_rent_spent: u64,
    pub bump: u8,
}

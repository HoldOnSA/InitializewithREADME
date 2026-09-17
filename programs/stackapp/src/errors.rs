use anchor_lang::prelude::*;

#[error_code]
pub enum StackError {
    #[msg("Tax curve must contain at least one point")]
    EmptyTaxCurve,
    #[msg("Tax curve has too many points")]
    TaxCurveTooLong,
    #[msg("Tax curve must start at seconds_held = 0")]
    TaxCurveMustStartAtZero,
    #[msg("Tax curve points must be strictly increasing in seconds_held")]
    TaxCurveNotSorted,
    #[msg("Tax curve must be non-increasing in tax_bps (holding longer may never cost more)")]
    TaxCurveNotMonotonic,
    #[msg("Tax rate exceeds the protocol maximum")]
    TaxRateTooHigh,
    #[msg("Vest duration is out of range")]
    InvalidVestDuration,
    #[msg("Amount must be greater than zero")]
    ZeroAmount,
    #[msg("Position has no spendable balance for this operation")]
    InsufficientSpendable,
    #[msg("Position has reached its FIFO lot capacity; call compact_lots first")]
    LotCapacityExceeded,
    #[msg("Arithmetic overflow")]
    MathOverflow,
    #[msg("Claim is not yet eligible: minimum block delay after a balance increase has not elapsed")]
    ClaimTooSoon,
    #[msg("Nothing to claim")]
    NothingToClaim,
    #[msg("Position has not reached maturity yet")]
    NotMatured,
    #[msg("Position was substantially exited; it does not qualify as held to maturity")]
    ExitedBeforeMaturity,
    #[msg("Reputation for this position was already credited for the current window")]
    ReputationAlreadyCredited,
    #[msg("Curve slippage exceeded the caller's limit")]
    SlippageExceeded,
    #[msg("Curve vault has insufficient lamports to settle this sale")]
    CurveInsolvent,
    #[msg("Cannot transfer a position to itself")]
    SelfTransfer,
    #[msg("Position does not belong to the expected mint")]
    MintMismatch,
    #[msg("Not enough lots to compact")]
    NothingToCompact,
    #[msg("Buy amount exceeds the curve's remaining token reserves")]
    CurveCapacityExceeded,
}

//! Tunable constants for the StackApp prototype.
//!
//! Everything here is denominated in raw units: token amounts are base units
//! (the prototype assumes 6 decimals), SOL amounts are lamports, durations are
//! seconds, and rates are basis points.

/// Basis-point denominator. 10_000 bps == 100%.
pub const BPS_DENOMINATOR: u64 = 10_000;

/// Fixed-point scale for `LoyaltyPool::acc_reward_per_share` (1e12).
///
/// The accumulator stores "reward per weighted share" scaled by this constant
/// so that integer division does not annihilate small distributions.
pub const ACC_PRECISION: u128 = 1_000_000_000_000;

/// Maximum number of points in a tax curve.
pub const MAX_TAX_CURVE_POINTS: usize = 8;

/// Maximum number of FIFO lots a single `Position` can hold before the owner
/// must call `compact_lots`.
pub const MAX_LOTS: usize = 24;

/// Hard ceiling on any single tax tier. Keeps an operator from configuring a
/// 100% honeypot tax.
pub const MAX_TAX_BPS: u16 = 9_000;

/// Minimum number of slots that must elapse between a `Position`'s last
/// balance *increase* (buy, inbound transfer, reward claim) and a
/// `claim_pool_share`.
///
/// This is the flash-loan / same-block guard: an attacker cannot buy, watch a
/// victim's taxed sell land in the same block, and claim against the inflated
/// `acc_reward_per_share` in that same block.
pub const MIN_CLAIM_DELAY_SLOTS: u64 = 4;

/// Upper bound on `vest_duration_seconds` (2 years).
pub const MAX_VEST_DURATION_SECONDS: i64 = 60 * 60 * 24 * 365 * 2;

/// Fraction of everything ever bought that must still be held for a position
/// to count as "held to maturity" rather than a full exit.
pub const MATURITY_MIN_RETENTION_BPS: u64 = 5_000;

/// Divisor turning `lamports * seconds` into reputation score points.
///
/// 1 SOL held for 1 day == 1_000_000_000 * 86_400 / 86_400_000_000 == 1000
/// points. So the score unit is "milli-SOL-days".
pub const REP_SCORE_DIVISOR: u128 = 86_400_000_000;

/// Tenure multiplier schedule: `(minimum_age_seconds, multiplier_bps)`.
///
/// Must be sorted ascending by age. The multiplier is deliberately *bounded*
/// (max 3x): an unbounded multiplier would make weight super-linear in time
/// and let a single ancient whale permanently capture the pool.
pub const TENURE_TIERS: [(i64, u64); 5] = [
    (0, 10_000),          // < 1 day   -> 1.00x
    (86_400, 12_500),     // >= 1 day  -> 1.25x
    (604_800, 15_000),    // >= 7 days -> 1.50x
    (2_592_000, 20_000),  // >= 30 days -> 2.00x
    (7_776_000, 30_000),  // >= 90 days -> 3.00x
];

/// Reputation tier score thresholds. Index i is the score needed for tier i+1.
pub const REPUTATION_TIER_THRESHOLDS: [u128; 4] = [1_000, 10_000, 50_000, 250_000];

/// Highest reachable reputation tier.
pub const MAX_REPUTATION_TIER: u8 = 4;

// ---------------------------------------------------------------------------
// PDA seeds
// ---------------------------------------------------------------------------

pub const SEED_CONFIG: &[u8] = b"config";
pub const SEED_POOL: &[u8] = b"pool";
pub const SEED_POSITION: &[u8] = b"position";
pub const SEED_REPUTATION: &[u8] = b"reputation";
pub const SEED_CURVE_VAULT: &[u8] = b"curve_vault";

//! Tunable constants for StackApp's pump.fun loyalty layer.
//!
//! Everything here is denominated in raw units: SOL amounts are lamports,
//! durations are seconds, and rates are basis points.

/// Basis-point denominator. 10_000 bps == 100%.
pub const BPS_DENOMINATOR: u64 = 10_000;

/// Fixed-point scale for `LoyaltyPool::acc_reward_per_share` (1e12).
///
/// The accumulator stores "reward per weighted share" scaled by this constant
/// so that integer division does not annihilate small distributions.
pub const ACC_PRECISION: u128 = 1_000_000_000_000;

/// Minimum number of slots that must elapse between a `Registration`'s last
/// weight *increase* (a `sync`/`claim` that saw a bigger balance, or a fresh
/// registration) and a `claim`.
///
/// This is the flash-loan / same-block guard: a wallet cannot buy into a
/// token, watch a fee land, and claim against the inflated
/// `acc_reward_per_share` in that same block - it has to carry real price
/// risk for several slots first.
pub const MIN_CLAIM_DELAY_SLOTS: u64 = 4;

/// Minimum seconds held for each tenure tier. Deliberately short: real
/// pump.fun token lifespans are usually minutes, not days or weeks.
pub const TENURE_TIER_SECONDS: [i64; 3] = [60, 600, 1_800]; // 1 min, 10 min, 30 min

/// Multiplier applied to live balance at each tenure tier, indexed the same
/// way as `TENURE_TIER_SECONDS` (index 0 is "held less than the first
/// threshold", i.e. the floor). Bounded on purpose, same reasoning as the
/// old design's tenure schedule: an unbounded multiplier would let the very
/// first holder permanently capture the pool no matter how much later
/// capital arrives.
pub const TENURE_TIER_MULTIPLIER_BPS: [u64; 4] = [10_000, 12_500, 15_000, 20_000]; // 1.00x / 1.25x / 1.50x / 2.00x

/// The fixed, distinctive marker amount a holder sends from their own wallet
/// to a token's `DepositVault` to register - a plain SOL transfer, not a
/// program interaction, so it works from any terminal. Non-refundable: it is
/// the registration cost, not a deposit.
///
/// Deliberately more than the "obvious" 0.001337 SOL: `write_registration`
/// funds the new `Registration` PDA's rent-exemption straight out of
/// `DepositVault` (see that instruction), and a `Registration` account's
/// rent-exemption alone is ~0.00179 SOL - a marker any smaller than that
/// would leave the very first registration on a token unable to pay for
/// itself.
pub const REGISTRATION_MARKER_LAMPORTS: u64 = 2_500_000; // 0.0025 SOL

// ---------------------------------------------------------------------------
// PDA seeds
// ---------------------------------------------------------------------------

pub const SEED_GLOBAL_CONFIG: &[u8] = b"global_config";
pub const SEED_CONFIG: &[u8] = b"config";
pub const SEED_POOL: &[u8] = b"pool";
pub const SEED_DEPOSIT_VAULT: &[u8] = b"deposit";
pub const SEED_REGISTRATION: &[u8] = b"registration";

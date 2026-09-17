"""Mirror of `programs/stackapp/src/constants.rs`.

Any change here must be mirrored there and vice versa; `tests/test_parity.py`
checks the two files agree.
"""

# Basis-point denominator. 10_000 bps == 100%.
BPS_DENOMINATOR = 10_000

# Fixed-point scale for LoyaltyPool.acc_reward_per_share (1e12).
ACC_PRECISION = 1_000_000_000_000

MAX_TAX_CURVE_POINTS = 8
MAX_LOTS = 24
MAX_TAX_BPS = 9_000

# Minimum slots between a balance increase and pool-claim eligibility.
MIN_CLAIM_DELAY_SLOTS = 4

MAX_VEST_DURATION_SECONDS = 60 * 60 * 24 * 365 * 2

# Fraction of everything ever bought that must still be held to count as
# "held to maturity".
MATURITY_MIN_RETENTION_BPS = 5_000

# lamports * seconds -> score points. 1 SOL for 1 day == 1000 points.
REP_SCORE_DIVISOR = 86_400_000_000

# (minimum_age_seconds, multiplier_bps), ascending. Bounded at 3x on purpose.
TENURE_TIERS = (
    (0, 10_000),          # < 1 day    -> 1.00x
    (86_400, 12_500),     # >= 1 day   -> 1.25x
    (604_800, 15_000),    # >= 7 days  -> 1.50x
    (2_592_000, 20_000),  # >= 30 days -> 2.00x
    (7_776_000, 30_000),  # >= 90 days -> 3.00x
)

REPUTATION_TIER_THRESHOLDS = (1_000, 10_000, 50_000, 250_000)
MAX_REPUTATION_TIER = 4

TIER_NAMES = ("Drifter", "Holder", "Anchor", "Keystone", "Bedrock")

# PDA seeds
SEED_CONFIG = b"config"
SEED_POOL = b"pool"
SEED_POSITION = b"position"
SEED_REPUTATION = b"reputation"
SEED_CURVE_VAULT = b"curve_vault"

# Prototype curve defaults: 30 SOL against ~1.073e9 whole tokens at 6 decimals.
DEFAULT_VIRTUAL_SOL_RESERVES = 30_000_000_000
DEFAULT_VIRTUAL_TOKEN_RESERVES = 1_073_000_000_000_000

LAMPORTS_PER_SOL = 1_000_000_000
DAY = 86_400

U64_MAX = 2**64 - 1

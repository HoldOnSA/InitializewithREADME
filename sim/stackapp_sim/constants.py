"""Mirror of `programs/stackapp/src/constants.rs`.

Any change here must be mirrored there and vice versa; `tests/test_parity.py`
checks the two files agree.
"""

# Basis-point denominator. 10_000 bps == 100%.
BPS_DENOMINATOR = 10_000

# Fixed-point scale for LoyaltyPool.acc_reward_per_share (1e12).
ACC_PRECISION = 1_000_000_000_000

# Minimum number of slots that must elapse between a Registration's last
# weight *increase* and a claim - the flash-loan / same-block guard.
MIN_CLAIM_DELAY_SLOTS = 4

# Minimum seconds held for each tenure tier. Deliberately short: real
# pump.fun token lifespans are usually minutes, not days or weeks.
TENURE_TIER_SECONDS = (60, 600, 1_800)  # 1 min, 10 min, 30 min

# Multiplier applied to live balance at each tenure tier, indexed the same
# way as TENURE_TIER_SECONDS (index 0 is "held less than the first
# threshold", i.e. the floor).
TENURE_TIER_MULTIPLIER_BPS = (10_000, 12_500, 15_000, 20_000)  # 1.00x/1.25x/1.50x/2.00x

# The fixed, distinctive marker amount a holder sends from their own wallet
# to a token's DepositVault to register - a plain SOL transfer, not a
# program interaction.
REGISTRATION_MARKER_LAMPORTS = 2_500_000  # 0.0025 SOL

# PDA seeds
SEED_GLOBAL_CONFIG = b"global_config"
SEED_CONFIG = b"config"
SEED_POOL = b"pool"
SEED_DEPOSIT_VAULT = b"deposit"
SEED_REGISTRATION = b"registration"

LAMPORTS_PER_SOL = 1_000_000_000
DAY = 86_400
MINUTE = 60

U64_MAX = 2**64 - 1

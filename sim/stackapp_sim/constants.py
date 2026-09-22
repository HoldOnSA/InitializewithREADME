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

# ---------------------------------------------------------------------------
# Rent-exemption, mirrored for vault-reconciliation realism only
# ---------------------------------------------------------------------------
#
# The real Solana rent-exemption formula: (128 + data_len) * lamports_per_byte_year
# * exemption_years. Rust computes this dynamically via `Rent::get()` rather than
# a `pub const`, so there's no `pub const` for `test_parity.py` to scrape these
# against directly - instead, `test_parity.py::test_registration_account_length_
# matches_the_real_struct` (and the DepositVault equivalent) parses the real
# struct field lists straight out of the Rust source and recomputes the byte
# length from them, so the two `*_ACCOUNT_LEN` constants below can't silently go
# stale if a field is ever added to, removed from, or retyped in either struct.
#
# What that check does NOT catch: `_RENT_LAMPORTS_PER_BYTE_YEAR` and
# `_RENT_EXEMPTION_YEARS` themselves are live Solana runtime parameters, not
# derived from anything in this repo, and nothing here queries a real cluster to
# confirm they still match `Rent::get()`'s actual values. Accepted deliberately,
# not an oversight: these are long-stable protocol-wide constants (unchanged
# since well before this program existed) that would need a cluster-dependent
# test to verify, which would cost the whole suite its "no network, no install"
# guarantee (see sim/README.md) for a risk this low. If they ever do change,
# `Market.reconcile()`'s numbers would drift from the real chain's until these
# two values are updated by hand - worth a quick cross-check against a live
# `getMinimumBalanceForRentExemption` RPC call or the `solana rent` CLI before
# this design goes anywhere near a real audit, not worth automating today.
_RENT_LAMPORTS_PER_BYTE_YEAR = 3_480
_RENT_EXEMPTION_YEARS = 2

# 8-byte discriminator + Registration's fields (owner 32, mint 32, registered_at
# 8, weighted_shares 8, reward_checkpoint 16, pending_rewards 8,
# lifetime_rewards_claimed 8, last_sync_slot 8, bump 1 = 121). Cross-checked
# against the real struct by test_parity.py - see the module note above.
REGISTRATION_ACCOUNT_LEN = 129
REGISTRATION_RENT_LAMPORTS = (
    (128 + REGISTRATION_ACCOUNT_LEN) * _RENT_LAMPORTS_PER_BYTE_YEAR * _RENT_EXEMPTION_YEARS
)

# 8-byte discriminator + DepositVault's fields (mint 32, total_marker_deposits 8,
# total_rent_spent 8, bump 1 = 49). Also cross-checked by test_parity.py.
DEPOSIT_VAULT_ACCOUNT_LEN = 57
DEPOSIT_VAULT_RENT_LAMPORTS = (
    (128 + DEPOSIT_VAULT_ACCOUNT_LEN) * _RENT_LAMPORTS_PER_BYTE_YEAR * _RENT_EXEMPTION_YEARS
)

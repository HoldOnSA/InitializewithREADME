"""StackApp simulation harness.

A faithful Python mirror of the Anchor program's math and state transitions, so
the accumulator and the anti-gaming rules can be exercised without a Solana
toolchain. See `sim/README.md`.

This is a *model*, not the deployed artifact. The Rust in `programs/stackapp`
is the real thing; `tests/test_parity.py` checks the two agree on the constants
they share.
"""

from .constants import (  # noqa: F401
    ACC_PRECISION,
    BPS_DENOMINATOR,
    DAY,
    LAMPORTS_PER_SOL,
    MIN_CLAIM_DELAY_SLOTS,
    MINUTE,
    TENURE_TIER_MULTIPLIER_BPS,
    TENURE_TIER_SECONDS,
)
from .logic import StackError  # noqa: F401
from .market import Market  # noqa: F401
from .state import DepositVault, GlobalConfig, LoyaltyPool, Registration, TokenConfig  # noqa: F401

__all__ = [
    "Market",
    "StackError",
    "GlobalConfig",
    "TokenConfig",
    "DepositVault",
    "Registration",
    "LoyaltyPool",
    "TENURE_TIER_SECONDS",
    "TENURE_TIER_MULTIPLIER_BPS",
]

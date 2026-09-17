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
    MAX_LOTS,
    MIN_CLAIM_DELAY_SLOTS,
    TIER_NAMES,
)
from .logic import StackError  # noqa: F401
from .market import Market  # noqa: F401
from .state import (  # noqa: F401
    ExitBreakdown,
    Lot,
    LoyaltyPool,
    Position,
    Reputation,
    TokenConfig,
)

# Handy presets matching the /launch page.
TAX_CURVE_PRESETS = {
    # name: [(seconds_held, tax_bps), ...]
    "diamond": [(0, 3_000), (3_600, 2_000), (86_400, 1_000), (604_800, 0)],
    "gentle": [(0, 1_000), (3_600, 500), (86_400, 200), (604_800, 0)],
    "brutal": [(0, 9_000), (3_600, 6_000), (86_400, 3_000), (2_592_000, 500)],
    "flat": [(0, 500)],
}

__all__ = [
    "Market",
    "StackError",
    "Position",
    "LoyaltyPool",
    "TokenConfig",
    "Reputation",
    "Lot",
    "ExitBreakdown",
    "TAX_CURVE_PRESETS",
    "TIER_NAMES",
]

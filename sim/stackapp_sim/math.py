"""Mirror of `programs/stackapp/src/math/`.

Pure integer arithmetic, floored/ceiled exactly as the Rust does, so a Python
result and an on-chain result agree bit for bit.
"""

from __future__ import annotations

from typing import List, Tuple

from .constants import (
    ACC_PRECISION,
    BPS_DENOMINATOR,
    TENURE_TIER_MULTIPLIER_BPS,
    TENURE_TIER_SECONDS,
    U64_MAX,
)


def ceil_div(a: int, b: int) -> int:
    """Ceiling division. Returns 0 when b == 0, matching the Rust."""
    if b == 0:
        return 0
    return -(-a // b)


# ---------------------------------------------------------------------------
# Reward-per-share accumulator
# ---------------------------------------------------------------------------


def distribute(acc: int, total_weighted_shares: int, amount: int) -> Tuple[int, int]:
    """Fold `amount` into the accumulator.

    Returns (new_acc, undistributed_remainder). Rounding always favours the
    pool, never the holder, so the pool can never become insolvent.
    """
    if amount == 0:
        return acc, 0
    if total_weighted_shares == 0:
        return acc, amount

    delta = (amount * ACC_PRECISION) // total_weighted_shares
    if delta == 0:
        # Too small to move the accumulator at this precision; buffer it.
        return acc, amount

    # Charge the accumulator the CEILING of what it just committed, not the
    # floor - see `programs/stackapp/src/math/accumulator.rs` for why.
    committed = min(ceil_div(delta * total_weighted_shares, ACC_PRECISION), amount)
    return acc + delta, amount - committed


def pending(weighted_shares: int, acc_now: int, checkpoint: int) -> int:
    """Rewards owed to a holder since their last checkpoint."""
    if weighted_shares == 0 or acc_now <= checkpoint:
        return 0
    return min((weighted_shares * (acc_now - checkpoint)) // ACC_PRECISION, U64_MAX)


# ---------------------------------------------------------------------------
# Tenure weighting
# ---------------------------------------------------------------------------


def tenure_multiplier_bps(seconds_held: int) -> int:
    """Step function: the multiplier of the last tier whose threshold has
    been crossed. Negative `seconds_held` is clamped to the floor tier."""
    age = max(seconds_held, 0)
    mult = TENURE_TIER_MULTIPLIER_BPS[0]
    for i, threshold in enumerate(TENURE_TIER_SECONDS):
        if age >= threshold:
            mult = TENURE_TIER_MULTIPLIER_BPS[i + 1]
        else:
            break
    return mult


def weight_for_balance(balance: int, seconds_held: int) -> int:
    """A registration's weighted shares: live balance x tenure multiplier."""
    mult = tenure_multiplier_bps(seconds_held)
    return (balance * mult) // BPS_DENOMINATOR


__all__: List[str] = [
    "ceil_div",
    "distribute",
    "pending",
    "tenure_multiplier_bps",
    "weight_for_balance",
]

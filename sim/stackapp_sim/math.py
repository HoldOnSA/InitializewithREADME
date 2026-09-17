"""Mirror of `programs/stackapp/src/math/`.

Pure integer arithmetic, floored/ceiled exactly as the Rust does, so a Python
result and an on-chain result agree bit for bit.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .constants import (
    ACC_PRECISION,
    BPS_DENOMINATOR,
    MAX_REPUTATION_TIER,
    MAX_TAX_BPS,
    MAX_TAX_CURVE_POINTS,
    REPUTATION_TIER_THRESHOLDS,
    REP_SCORE_DIVISOR,
    TENURE_TIERS,
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
    # floor. The exact entitlement `delta * total / ACC` is usually fractional;
    # buffering `amount - floor(...)` would leave that fraction claimable in the
    # accumulator *and* re-buffer it for redistribution, over-issuing up to one
    # unit per distribution. Rounding the charge up cannot exceed `amount`,
    # because `delta` was itself floored.
    committed = min(ceil_div(delta * total_weighted_shares, ACC_PRECISION), amount)
    return acc + delta, amount - committed


def pending(weighted_shares: int, acc_now: int, checkpoint: int) -> int:
    """Rewards owed to a holder since their last checkpoint."""
    if weighted_shares == 0 or acc_now <= checkpoint:
        return 0
    return min((weighted_shares * (acc_now - checkpoint)) // ACC_PRECISION, U64_MAX)


# ---------------------------------------------------------------------------
# Tax curve
# ---------------------------------------------------------------------------

TaxPoint = Tuple[int, int]  # (seconds_held, tax_bps)


class InvalidTaxCurve(ValueError):
    pass


def validate_tax_curve(points: Sequence[TaxPoint]) -> None:
    """Raise InvalidTaxCurve unless the curve is well formed."""
    if not points:
        raise InvalidTaxCurve("EmptyTaxCurve")
    if len(points) > MAX_TAX_CURVE_POINTS:
        raise InvalidTaxCurve("TaxCurveTooLong")
    if points[0][0] != 0:
        raise InvalidTaxCurve("TaxCurveMustStartAtZero")
    for i, (secs, bps) in enumerate(points):
        if bps > MAX_TAX_BPS:
            raise InvalidTaxCurve("TaxRateTooHigh")
        if i > 0:
            if secs <= points[i - 1][0]:
                raise InvalidTaxCurve("TaxCurveNotSorted")
            if bps > points[i - 1][1]:
                # A rising curve would create a "sell now before the tax goes
                # up" cliff, which is the opposite of the point.
                raise InvalidTaxCurve("TaxCurveNotMonotonic")


def tax_bps_for_age(points: Sequence[TaxPoint], age_seconds: int) -> int:
    """Step function: the rate of the last point whose seconds_held <= age."""
    if not points:
        return 0
    age = max(age_seconds, 0)
    rate = points[0][1]
    for secs, bps in points:
        if secs <= age:
            rate = bps
        else:
            break
    return rate


def tax_for_amount(amount: int, bps: int) -> int:
    """Tax owed, rounded UP.

    Rounding up is an anti-gaming rule in itself: with floor rounding an exit
    split into dust would pay zero tax.
    """
    if amount == 0 or bps == 0:
        return 0
    return min(ceil_div(amount * bps, BPS_DENOMINATOR), amount)


# ---------------------------------------------------------------------------
# Vesting
# ---------------------------------------------------------------------------


def vested_amount(original: int, buy_timestamp: int, now: int, vest_duration_seconds: int) -> int:
    """How much of `original` linear vesting has unlocked by `now`."""
    if original == 0:
        return 0
    if vest_duration_seconds <= 0:
        return original
    if now <= buy_timestamp:
        return 0
    elapsed = now - buy_timestamp
    if elapsed >= vest_duration_seconds:
        return original
    return (original * elapsed) // vest_duration_seconds


def vested_bps(buy_timestamp: int, now: int, vest_duration_seconds: int) -> int:
    if vest_duration_seconds <= 0:
        return BPS_DENOMINATOR
    if now <= buy_timestamp:
        return 0
    elapsed = now - buy_timestamp
    if elapsed >= vest_duration_seconds:
        return BPS_DENOMINATOR
    return (elapsed * BPS_DENOMINATOR) // vest_duration_seconds


# ---------------------------------------------------------------------------
# Tenure weighting and reputation
# ---------------------------------------------------------------------------


def tenure_multiplier_bps(age_seconds: int) -> int:
    age = max(age_seconds, 0)
    mult = TENURE_TIERS[0][1]
    for min_age, m in TENURE_TIERS:
        if min_age <= age:
            mult = m
        else:
            break
    return mult


def reputation_score_delta(capital_lamports: int, window_seconds: int) -> int:
    """Linear in capital and in time. Unit: milli-SOL-days.

    Linearity is the anti-sybil argument. A concave function would pay
    n * f(C/n) > f(C) and make sharding profitable; linear pays the same
    either way, and the floor makes sharding weakly worse.
    """
    if capital_lamports == 0 or window_seconds <= 0:
        return 0
    return (capital_lamports * window_seconds) // REP_SCORE_DIVISOR


def tier_for_score(score: int) -> int:
    tier = 0
    for threshold in REPUTATION_TIER_THRESHOLDS:
        if score >= threshold:
            tier += 1
        else:
            break
    return min(tier, MAX_REPUTATION_TIER)


# ---------------------------------------------------------------------------
# Bonding curve (constant product, virtual reserves)
# ---------------------------------------------------------------------------


def buy_cost_lamports(v_sol: int, v_tok: int, amount: int):
    """Lamports to buy `amount` base units. None if it would drain the curve.

    Rounds the cost UP, which is what keeps `k` non-decreasing.
    """
    if amount == 0:
        return 0
    if amount >= v_tok:
        return None
    k = v_sol * v_tok
    new_sol = ceil_div(k, v_tok - amount)
    if new_sol > U64_MAX:
        return None
    return new_sol - v_sol


def sell_proceeds_lamports(v_sol: int, v_tok: int, amount: int) -> int:
    """Lamports returned for selling `amount` back. Rounds proceeds DOWN."""
    if amount == 0:
        return 0
    k = v_sol * v_tok
    new_sol = min(ceil_div(k, v_tok + amount), U64_MAX)
    return max(v_sol - new_sol, 0)


def spot_price_lamports(v_sol: int, v_tok: int, decimals: int) -> int:
    """Lamports per whole token."""
    if v_tok == 0:
        return 0
    return min((v_sol * (10 ** min(decimals, 18))) // v_tok, U64_MAX)


def weighted_shares_for_lots(lots: Iterable, now: int) -> int:
    """Sum of per-lot weight. Imported lazily to avoid a circular import."""
    total = 0
    for lot in lots:
        total += lot_weight(lot, now)
    return total


def lot_weight(lot, now: int) -> int:
    remaining = lot.remaining()
    if remaining == 0:
        return 0
    mult = tenure_multiplier_bps(now - lot.buy_timestamp)
    return (remaining * mult) // BPS_DENOMINATOR


__all__: List[str] = [
    "ceil_div",
    "distribute",
    "pending",
    "validate_tax_curve",
    "InvalidTaxCurve",
    "tax_bps_for_age",
    "tax_for_amount",
    "vested_amount",
    "vested_bps",
    "tenure_multiplier_bps",
    "reputation_score_delta",
    "tier_for_score",
    "buy_cost_lamports",
    "sell_proceeds_lamports",
    "spot_price_lamports",
    "lot_weight",
    "weighted_shares_for_lots",
]

"""Mirror of `programs/stackapp/src/logic.rs`.

Ordering is the same as on chain and it is load-bearing:

    touch_position()   # flush buffered tax, then settle at the OLD weight
    ...mutate lots...
    refresh_weight()   # re-price weight, adjust the pool total
    collect_tax()      # only then distribute new tax

Settling before re-weighting is what stops a buy from earning retroactively;
re-weighting before collecting is what stops a seller from collecting on their
own tax at a weight they no longer hold.
"""

from __future__ import annotations

from typing import Sequence

from .constants import (
    BPS_DENOMINATOR,
    MATURITY_MIN_RETENTION_BPS,
    MAX_LOTS,
    MIN_CLAIM_DELAY_SLOTS,
)
from .math import (
    distribute,
    pending,
    tax_bps_for_age,
    tax_for_amount,
    vested_amount,
    weighted_shares_for_lots,
)
from .state import ExitBreakdown, Lot, LoyaltyPool, Position


class StackError(Exception):
    """Mirrors `errors.rs`; the message is the Rust variant name."""


# ---------------------------------------------------------------------------
# Pool accounting
# ---------------------------------------------------------------------------


def flush_undistributed(pool: LoyaltyPool) -> None:
    """Push buffered tax into the accumulator, if there is weight for it."""
    if pool.undistributed == 0 or pool.total_weighted_shares == 0:
        return
    acc, leftover = distribute(
        pool.acc_reward_per_share, pool.total_weighted_shares, pool.undistributed
    )
    pool.acc_reward_per_share = acc
    pool.undistributed = leftover


def settle_position(position: Position, pool: LoyaltyPool) -> None:
    """Accrue rewards up to the pool's current accumulator."""
    position.pending_rewards += pending(
        position.weighted_shares, pool.acc_reward_per_share, position.reward_checkpoint
    )
    position.reward_checkpoint = pool.acc_reward_per_share


def touch_position(position: Position, pool: LoyaltyPool, now: int = 0) -> None:
    flush_undistributed(pool)
    settle_position(position, pool)


def refresh_weight(position: Position, pool: LoyaltyPool, now: int) -> None:
    """Re-price tenure weight and reconcile the pool total."""
    new_weight = weighted_shares_for_lots(position.lots, now)
    pool.total_weighted_shares = pool.total_weighted_shares - position.weighted_shares + new_weight
    position.weighted_shares = new_weight


def collect_tax(pool: LoyaltyPool, amount: int) -> None:
    """Route tax into the pool. Buffers first, then flushes, so a distribution
    that lands with zero weight is held rather than burned."""
    if amount == 0:
        return
    pool.total_collected += amount
    pool.undistributed += amount
    flush_undistributed(pool)


def claim_is_eligible(position: Position, current_slot: int) -> bool:
    """The flash-loan / same-block guard."""
    return current_slot >= position.last_increase_slot + MIN_CLAIM_DELAY_SLOTS


# ---------------------------------------------------------------------------
# Lots
# ---------------------------------------------------------------------------


def push_vesting_lot(position: Position, amount: int, now: int) -> None:
    if amount <= 0:
        raise StackError("ZeroAmount")
    if position.lots:
        last = position.lots[-1]
        if last.buy_timestamp == now and last.cum_released == 0:
            last.original += amount
            return
    if len(position.lots) >= MAX_LOTS:
        raise StackError("LotCapacityExceeded")
    position.lots.append(Lot.new(amount, now))


def push_liquid_lot(position: Position, amount: int, now: int) -> None:
    """Immediately spendable, but stamped `now` so dumping pays the top rate."""
    if amount <= 0:
        raise StackError("ZeroAmount")
    if position.lots:
        last = position.lots[-1]
        if last.buy_timestamp == now and last.cum_released == last.original:
            last.original += amount
            last.cum_released += amount
            last.released += amount
            position.spendable += amount
            return
    if len(position.lots) >= MAX_LOTS:
        raise StackError("LotCapacityExceeded")
    position.lots.append(Lot.liquid(amount, now))
    position.spendable += amount


def prune_empty_lots(position: Position) -> None:
    position.lots = [lot for lot in position.lots if not lot.is_empty()]


def compact_oldest_lots(position: Position) -> None:
    """Merge the two oldest lots, taking the NEWER timestamp.

    Owner-initiated only, so compaction is always a cost to the owner and can
    never launder fresh tokens into an old lot's rate.
    """
    if len(position.lots) < 2:
        raise StackError("NothingToCompact")
    b = position.lots.pop(1)
    a = position.lots[0]
    a.original += b.original
    a.cum_released += b.cum_released
    a.released += b.released
    a.buy_timestamp = max(a.buy_timestamp, b.buy_timestamp)


def release_vested(position: Position, vest_duration_seconds: int, now: int) -> int:
    """Move everything vesting has unlocked into the spendable balance."""
    total = 0
    for lot in position.lots:
        target = vested_amount(lot.original, lot.buy_timestamp, now, vest_duration_seconds)
        delta = max(target - lot.cum_released, 0)
        if delta == 0:
            continue
        lot.cum_released += delta
        lot.released += delta
        total += delta
    if total:
        position.spendable += total
        position.vested_claimed += total
    return total


def consume_spendable(
    position: Position,
    amount: int,
    tax_curve: Sequence[tuple],
    now: int,
    tax_exempt: bool = False,
) -> ExitBreakdown:
    """Remove `amount` spendable tokens, oldest lot first.

    The single code path behind EVERY balance decrease - a sell, a
    wallet-to-wallet transfer, or a pool donation. Only `tax_exempt` (reserved
    for the program's own pool address) skips the tax, and that path gives the
    tokens away, so there is nothing to game.
    """
    if amount <= 0:
        raise StackError("ZeroAmount")
    if position.spendable < amount:
        raise StackError("InsufficientSpendable")

    total_remaining_before = position.total_remaining()
    out = ExitBreakdown()
    remaining = amount

    # `lots` is append-ordered, so iterating forward is FIFO by buy_timestamp.
    for lot in position.lots:
        if remaining == 0:
            break
        if lot.released == 0:
            continue
        take = min(lot.released, remaining)
        age = now - lot.buy_timestamp
        bps = 0 if tax_exempt else tax_bps_for_age(tax_curve, age)
        tax = tax_for_amount(take, bps)

        out.tax += tax
        out.net += take - tax
        out.tenure_weighted_volume += take * max(age, 0)
        out.top_tax_bps = max(out.top_tax_bps, bps)

        lot.released -= take
        remaining -= take

    if remaining != 0:
        raise StackError("InsufficientSpendable")

    out.gross = amount
    position.spendable -= amount
    position.total_sold += amount
    position.tenure_weighted_volume += out.tenure_weighted_volume

    # Release cost basis pro-rata to the fraction of the position leaving.
    if total_remaining_before > 0 and position.cost_basis_lamports > 0:
        released = min(
            (position.cost_basis_lamports * amount) // total_remaining_before,
            position.cost_basis_lamports,
        )
        position.cost_basis_lamports -= released
        out.cost_basis_released = released

    prune_empty_lots(position)
    return out


def retained_through_maturity(position: Position) -> bool:
    """Did this position keep enough of what it bought to count as matured?"""
    if position.total_bought == 0:
        return False
    remaining = position.total_remaining()
    threshold = (position.total_bought * MATURITY_MIN_RETENTION_BPS) // BPS_DENOMINATOR
    return remaining >= threshold and remaining > 0

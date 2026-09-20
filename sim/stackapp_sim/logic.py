"""Mirror of `programs/stackapp/src/logic.rs`.

Ordering is the same as on chain and it is load-bearing:

    touch_registration()   # flush buffered fee, then settle at the OLD weight
    refresh_weight()       # re-price weight from the LIVE balance, adjust the pool total
    collect_fee()          # only then distribute newly pulled-in fee

Settling before re-weighting is what stops a bigger balance from earning
retroactively; re-weighting before collecting is what stops a claimer from
collecting on their own just-pulled fee at a weight they no longer have (or
haven't yet earned).
"""

from __future__ import annotations

from .constants import MIN_CLAIM_DELAY_SLOTS
from .math import distribute, pending, weight_for_balance
from .state import LoyaltyPool, Registration


class StackError(Exception):
    """Mirrors `errors.rs`; the message is the Rust variant name."""


def flush_undistributed(pool: LoyaltyPool) -> None:
    """Push any buffered fee into the accumulator, if there is weight for it."""
    if pool.undistributed == 0 or pool.total_weighted_shares == 0:
        return
    acc, leftover = distribute(
        pool.acc_reward_per_share, pool.total_weighted_shares, pool.undistributed
    )
    pool.acc_reward_per_share = acc
    pool.undistributed = leftover


def settle_registration(registration: Registration, pool: LoyaltyPool) -> None:
    """Accrue a registration's rewards up to the pool's current accumulator."""
    owed = pending(
        registration.weighted_shares, pool.acc_reward_per_share, registration.reward_checkpoint
    )
    registration.pending_rewards += owed
    registration.reward_checkpoint = pool.acc_reward_per_share


def touch_registration(registration: Registration, pool: LoyaltyPool) -> None:
    """Flush the pool, then settle the registration."""
    flush_undistributed(pool)
    settle_registration(registration, pool)


def refresh_weight(registration: Registration, pool: LoyaltyPool, live_balance: int, now: int) -> None:
    """Re-price a registration's weight from a *live* balance and reconcile
    the pool total. `live_balance` must come from an actual on-chain SPL
    token account read by the caller, never a cached/indexer-reported number.
    """
    new_weight = weight_for_balance(live_balance, now - registration.registered_at)
    pool.total_weighted_shares = pool.total_weighted_shares - registration.weighted_shares + new_weight
    registration.weighted_shares = new_weight


def collect_fee(pool: LoyaltyPool, amount: int) -> None:
    """Route `amount` of newly pulled-in fee into the pool.

    Always buffers first and then flushes, so a fee that lands while
    `total_weighted_shares == 0` is held rather than burned.
    """
    if amount == 0:
        return
    pool.total_collected += amount
    pool.undistributed += amount
    flush_undistributed(pool)


def claim_is_eligible(registration: Registration, current_slot: int) -> bool:
    """Whether `registration` has waited out the anti-flash-loan delay."""
    return current_slot >= registration.last_sync_slot + MIN_CLAIM_DELAY_SLOTS

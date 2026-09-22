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


def vault_expected_balance(
    own_rent_floor: int,
    total_marker_deposits: int,
    total_rent_spent: int,
    total_collected: int,
    total_claimed: int,
) -> int:
    """How much of a DepositVault's real lamport balance the program's own
    bookkeeping already explains, without it being fee revenue. Three things
    count:

    - `own_rent_floor`: the vault account's own rent-exemption. Never
      spendable, never anyone's reward.
    - `total_marker_deposits - total_rent_spent`: registration-marker money
      received but not yet consumed by the rent `write_registration` pays out
      of the vault to create each Registration PDA. `REGISTRATION_MARKER_LAMPORTS`
      is deliberately more than one PDA's rent, so this is normally positive
      and grows by a fixed amount per registration - the non-refundable
      "registration cost" float, not a reward.
    - `total_collected - total_claimed`: real lamports `donate` has already
      moved into the vault and folded into the accumulator, minus whatever
      `claim` has already paid back out of it. Already fee revenue, already
      counted - not to be swept a second time.

    Every subtraction clamps to 0 rather than going negative, mirroring
    Rust's `saturating_sub`: this has no side effects and must never be able
    to brick a permissionless crank over an arithmetic edge case.
    """
    retained_marker_float = max(total_marker_deposits - total_rent_spent, 0)
    backed_by_pool = max(total_collected - total_claimed, 0)
    return own_rent_floor + retained_marker_float + backed_by_pool


def vault_surplus(
    actual_lamports: int,
    own_rent_floor: int,
    total_marker_deposits: int,
    total_rent_spent: int,
    total_collected: int,
    total_claimed: int,
) -> int:
    """How much of a DepositVault's real lamport balance is unaccounted for by
    anything the program already tracks - i.e. arrived as a plain SOL transfer
    that went through neither `donate` nor the registration-marker flow.
    Returns 0 if nothing is unaccounted for (including, deliberately, when
    `actual_lamports` falls *under* `vault_expected_balance` - that's a
    deficit, not a surplus; callers that need to tell the two apart should
    compare against `vault_expected_balance` directly, the way
    `Market.reconcile()` does to raise a distinguishable signal).
    """
    expected = vault_expected_balance(
        own_rent_floor, total_marker_deposits, total_rent_spent, total_collected, total_claimed
    )
    return max(actual_lamports - expected, 0)

"""Mirror of `programs/stackapp/src/state/`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .constants import DEFAULT_VIRTUAL_SOL_RESERVES, DEFAULT_VIRTUAL_TOKEN_RESERVES


@dataclass
class Lot:
    """A single FIFO purchase lot.

    `original` never changes. `cum_released` is the monotonic amount vesting has
    ever unlocked; `released` is how much of that is still sitting spendable.
    Everything else is derived, which keeps the invariants checkable:

        locked    = original - cum_released
        sold      = cum_released - released
        remaining = original - sold = locked + released
    """

    original: int
    buy_timestamp: int
    cum_released: int = 0
    released: int = 0

    @classmethod
    def new(cls, amount: int, buy_timestamp: int) -> "Lot":
        return cls(original=amount, buy_timestamp=buy_timestamp)

    @classmethod
    def liquid(cls, amount: int, buy_timestamp: int) -> "Lot":
        """A lot that is spendable immediately but stamped with `now`."""
        return cls(
            original=amount,
            buy_timestamp=buy_timestamp,
            cum_released=amount,
            released=amount,
        )

    def locked(self) -> int:
        return max(self.original - self.cum_released, 0)

    def remaining(self) -> int:
        return self.locked() + self.released

    def is_empty(self) -> bool:
        return self.remaining() == 0


@dataclass
class Position:
    """Per (wallet, mint) ledger. This account *is* the holder's balance."""

    owner: str = ""
    mint: str = ""
    lots: List[Lot] = field(default_factory=list)

    vested_claimed: int = 0
    spendable: int = 0

    weighted_shares: int = 0
    reward_checkpoint: int = 0
    pending_rewards: int = 0
    lifetime_rewards_claimed: int = 0

    last_increase_slot: int = 0

    cost_basis_lamports: int = 0
    total_bought: int = 0
    total_sold: int = 0
    first_buy_timestamp: int = 0
    tenure_weighted_volume: int = 0
    credited_volume: int = 0
    last_reputation_timestamp: int = 0
    maturity_credits: int = 0

    def total_remaining(self) -> int:
        return sum(lot.remaining() for lot in self.lots)

    def total_locked(self) -> int:
        return sum(lot.locked() for lot in self.lots)


@dataclass
class LoyaltyPool:
    """Per-mint loyalty pool using the O(1) reward-per-share accumulator."""

    mint: str = ""
    acc_reward_per_share: int = 0
    total_weighted_shares: int = 0
    total_collected: int = 0
    total_claimed: int = 0
    # Tax collected while total_weighted_shares == 0, plus rounding dust.
    # Flushed as soon as there is weight to receive it, so nothing is burned.
    undistributed: int = 0


@dataclass
class TokenConfig:
    mint: str = ""
    creator: str = ""
    launch_timestamp: int = 0
    vest_duration_seconds: int = 0
    pool_account: str = ""
    curve_vault: str = ""
    tax_curve: List[tuple] = field(default_factory=list)

    virtual_sol_reserves: int = DEFAULT_VIRTUAL_SOL_RESERVES
    virtual_token_reserves: int = DEFAULT_VIRTUAL_TOKEN_RESERVES
    real_sol_reserves: int = 0
    tokens_sold: int = 0

    total_buy_volume_tokens: int = 0
    total_sell_volume_tokens: int = 0
    holder_count: int = 0
    decimals: int = 6


@dataclass
class Reputation:
    """Platform-wide, non-transferable, keyed by wallet."""

    owner: str = ""
    score: int = 0
    tier: int = 0
    tokens_held_to_maturity: int = 0
    total_tenure_weighted_volume: int = 0
    first_seen_timestamp: int = 0
    last_update_timestamp: int = 0


@dataclass
class ExitBreakdown:
    """Result of removing tokens from a position."""

    gross: int = 0
    tax: int = 0
    net: int = 0
    tenure_weighted_volume: int = 0
    cost_basis_released: int = 0
    top_tax_bps: int = 0

"""Mirror of `programs/stackapp/src/state/`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GlobalConfig:
    """Singleton. PDA: `["global_config"]`."""

    authority: str = ""
    bump: int = 0


@dataclass
class TokenConfig:
    """Per-mint registration record. PDA: `["config", mint]`."""

    mint: str = ""
    creator: str = ""
    registered_at: int = 0
    deposit_vault: str = ""
    deposit_bump: int = 0
    bump: int = 0


@dataclass
class DepositVault:
    """A single token's unique fee-deposit and registration address.

    PDA: `["deposit", mint]`.
    """

    mint: str = ""
    # Sum of REGISTRATION_MARKER_LAMPORTS over every write_registration ever
    # called for this mint - a proxy for "markers received", since a marker
    # transfer itself is invisible on chain. Together with total_rent_spent,
    # lets `Market.reconcile()` tell "marker money not yet consumed by rent"
    # apart from real fee revenue.
    total_marker_deposits: int = 0
    # Sum of the rent-exemption write_registration has ever paid out of this
    # vault to create Registration PDAs.
    total_rent_spent: int = 0
    bump: int = 0


@dataclass
class Registration:
    """Per (wallet, mint) loyalty record. PDA: `["registration", mint, owner]`."""

    owner: str = ""
    mint: str = ""
    registered_at: int = 0
    weighted_shares: int = 0
    reward_checkpoint: int = 0
    pending_rewards: int = 0
    lifetime_rewards_claimed: int = 0
    last_sync_slot: int = 0
    bump: int = 0


@dataclass
class LoyaltyPool:
    """Per-mint loyalty pool using the O(1) reward-per-share accumulator.

    PDA: `["pool", mint]`.
    """

    mint: str = ""
    acc_reward_per_share: int = 0
    total_weighted_shares: int = 0
    total_collected: int = 0
    total_claimed: int = 0
    # Fees collected while total_weighted_shares == 0, plus rounding dust.
    # Flushed as soon as there is weight to receive it, so nothing is burned.
    undistributed: int = 0
    bump: int = 0

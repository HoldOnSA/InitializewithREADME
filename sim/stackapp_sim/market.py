"""An in-process mirror of the instruction handlers.

`Market` does what the on-chain program does, in the same order, against the
same state structs - including `DepositVault`'s real lamport balance. Any
divergence between this and `programs/stackapp/src/instructions/` is a bug in
one of them.

Unlike the old bonding-curve design, StackApp doesn't run the trade for this
token - a holder's balance moves entirely on pump.fun's own program, outside
this simulation. `Market.balances` stands in for "the live SPL balance a
`sync`/`claim` would read on chain"; tests move it directly with
`set_balance`, exactly the way `sync`/`claim` treat it as an external input
they never mutate themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .constants import (
    DEPOSIT_VAULT_RENT_LAMPORTS,
    REGISTRATION_MARKER_LAMPORTS,
    REGISTRATION_RENT_LAMPORTS,
)
from .logic import (
    StackError,
    claim_is_eligible,
    collect_fee,
    refresh_weight,
    touch_registration,
    vault_expected_balance,
)
from .state import DepositVault, LoyaltyPool, Registration, TokenConfig


@dataclass
class Event:
    kind: str
    payload: dict


@dataclass
class Market:
    """One registered pump.fun token, its pool, and every wallet's registration."""

    config: TokenConfig = field(default_factory=TokenConfig)
    pool: LoyaltyPool = field(default_factory=LoyaltyPool)
    vault: DepositVault = field(default_factory=DepositVault)
    registrations: Dict[str, Registration] = field(default_factory=dict)

    # The live SPL balance pump.fun's own program would report for each
    # wallet. Never mutated by anything in this module except `set_balance` -
    # `sync`/`claim` only ever read it, matching the on-chain design.
    balances: Dict[str, int] = field(default_factory=dict)

    # DepositVault's real lamport balance - registration markers and
    # donations both land here, and claim pays out of it directly.
    vault_lamports: int = 0
    # Lamports each wallet has received via claim, for round-trip accounting.
    wallet_lamports: Dict[str, int] = field(default_factory=dict)

    now: int = 0
    slot: int = 0
    events: List[Event] = field(default_factory=list)

    # -- setup --------------------------------------------------------------

    @classmethod
    def register(
        cls,
        mint: str = "MINT",
        creator: str = "creator",
        now: int = 0,
        slot: int = 0,
    ) -> "Market":
        """Mirrors `register_mint`: start tracking a pump.fun mint."""
        config = TokenConfig(
            mint=mint,
            creator=creator,
            registered_at=now,
            deposit_vault=f"deposit:{mint}",
        )
        m = cls(
            config=config,
            pool=LoyaltyPool(mint=mint),
            vault=DepositVault(mint=mint),
            # Mirrors reality: `register_mint`'s payer funds DepositVault's own
            # rent-exemption immediately on `init`. A freshly registered token
            # has no marker/donation money yet, so this is its whole balance.
            vault_lamports=DEPOSIT_VAULT_RENT_LAMPORTS,
            now=now,
            slot=slot,
        )
        m._emit("MintRegistered", mint=mint, creator=creator, deposit_vault=config.deposit_vault)
        return m

    def _emit(self, _name: str, **payload) -> None:
        payload.setdefault("timestamp", self.now)
        self.events.append(Event(_name, payload))

    def advance(self, seconds: int = 0, slots: int = 0) -> "Market":
        self.now += seconds
        # Devnet runs ~2.5 slots/second; default to that when only time moves.
        self.slot += slots if slots else max(seconds * 5 // 2, 0)
        return self

    def registration(self, wallet: str) -> Registration:
        r = self.registrations.get(wallet)
        if r is None:
            raise StackError("AccountNotInitialized")
        return r

    def set_balance(self, wallet: str, amount: int) -> None:
        """Stand-in for a real SPL balance change on pump.fun's own program."""
        self.balances[wallet] = amount

    def balance_of(self, wallet: str) -> int:
        return self.balances.get(wallet, 0)

    # -- instructions -------------------------------------------------------

    def write_registration(self, wallet: str) -> Registration:
        """Mirrors `write_registration`: the indexer authority writes this
        after observing `wallet` send `REGISTRATION_MARKER_LAMPORTS` to
        `DepositVault`. Honor-system: nothing here checks the marker itself -
        see `SECURITY_NOTES.md`.

        The marker transfer landing and the rent this instruction pays out of
        `DepositVault` to create the Registration PDA are both real lamport
        movements on chain, so both are modelled here: `vault_lamports` gains
        the marker and loses the rent, and the vault's own counters track
        both cumulatively - this is exactly what `reconcile()` needs to tell
        "marker money not yet consumed by rent" apart from real fee revenue.
        """
        if wallet in self.registrations:
            raise StackError("AccountAlreadyInitialized")
        r = Registration(owner=wallet, mint=self.config.mint, registered_at=self.now)
        self.registrations[wallet] = r
        self.vault_lamports += REGISTRATION_MARKER_LAMPORTS
        self.vault_lamports -= REGISTRATION_RENT_LAMPORTS
        self.vault.total_marker_deposits += REGISTRATION_MARKER_LAMPORTS
        self.vault.total_rent_spent += REGISTRATION_RENT_LAMPORTS
        self._emit("WalletRegistered", mint=self.config.mint, owner=wallet, registered_at=self.now)
        return r

    def sync(self, wallet: str) -> int:
        """Permissionless: re-price a registration's weight from its live balance."""
        r = self.registration(wallet)
        previous = r.weighted_shares
        touch_registration(r, self.pool)
        refresh_weight(r, self.pool, self.balance_of(wallet), self.now)
        if r.weighted_shares > previous:
            r.last_sync_slot = self.slot
        self._emit(
            "WeightSynced",
            mint=self.config.mint,
            owner=wallet,
            balance=self.balance_of(wallet),
            previous_weight=previous,
            new_weight=r.weighted_shares,
            total_weighted_shares=self.pool.total_weighted_shares,
        )
        return r.weighted_shares

    def donate(self, donor: str, amount: int) -> None:
        """Permissionless: voluntarily route lamports into the pool - the
        only source of real fee revenue. See `SECURITY_NOTES.md` for why
        there's no automatic pull from pump.fun."""
        if amount <= 0:
            raise StackError("ZeroDonation")
        self.vault_lamports += amount
        self.wallet_lamports[donor] = self.wallet_lamports.get(donor, 0) - amount
        collect_fee(self.pool, amount)
        self._emit(
            "FeeCollected",
            mint=self.config.mint,
            amount=amount,
            acc_reward_per_share=self.pool.acc_reward_per_share,
            total_weighted_shares=self.pool.total_weighted_shares,
            total_collected=self.pool.total_collected,
            undistributed=self.pool.undistributed,
        )

    def reconcile(self) -> int:
        """Permissionless: sweep any of the vault's balance that `donate` and
        the registration-marker bookkeeping can't already explain into the
        pool as fee revenue - see `logic.vault_expected_balance`. This is
        what picks up a plain SOL transfer landing in the vault outside of
        `donate`, which would otherwise sit there forever, invisible to
        every registration's reward math.

        If the vault's real balance ever came in *under* what its own
        bookkeeping says it should hold - should never happen under correct
        operation - this emits `VaultDeficitDetected` before raising, the
        same way `reconcile.rs`'s handler does: a real occurrence needs to
        be distinguishable from the routine "nothing new arrived yet" case,
        not silently folded into the same `NothingToReconcile` outcome.
        """
        expected = vault_expected_balance(
            DEPOSIT_VAULT_RENT_LAMPORTS,
            self.vault.total_marker_deposits,
            self.vault.total_rent_spent,
            self.pool.total_collected,
            self.pool.total_claimed,
        )
        if self.vault_lamports < expected:
            self._emit(
                "VaultDeficitDetected",
                mint=self.config.mint,
                deposit_vault=self.config.deposit_vault,
                actual_lamports=self.vault_lamports,
                expected_lamports=expected,
                deficit=expected - self.vault_lamports,
            )

        surplus = max(self.vault_lamports - expected, 0)
        if surplus == 0:
            raise StackError("NothingToReconcile")

        collect_fee(self.pool, surplus)
        self._emit(
            "FeeCollected",
            mint=self.config.mint,
            amount=surplus,
            acc_reward_per_share=self.pool.acc_reward_per_share,
            total_weighted_shares=self.pool.total_weighted_shares,
            total_collected=self.pool.total_collected,
            undistributed=self.pool.undistributed,
        )
        return surplus

    def claim(self, wallet: str) -> int:
        """Sync the caller's own weight, then pay out their accumulator share."""
        r = self.registration(wallet)
        if not claim_is_eligible(r, self.slot):
            raise StackError("ClaimTooSoon")

        touch_registration(r, self.pool)
        previous_weight = r.weighted_shares
        refresh_weight(r, self.pool, self.balance_of(wallet), self.now)
        if r.weighted_shares > previous_weight:
            r.last_sync_slot = self.slot

        payout = r.pending_rewards
        if payout == 0:
            raise StackError("NothingToClaim")
        r.pending_rewards = 0
        r.lifetime_rewards_claimed += payout
        self.pool.total_claimed += payout

        if payout > self.vault_lamports:
            raise StackError("MathOverflow")
        self.vault_lamports -= payout
        self.wallet_lamports[wallet] = self.wallet_lamports.get(wallet, 0) + payout

        self._emit(
            "RewardClaimed",
            mint=self.config.mint,
            owner=wallet,
            amount=payout,
            weighted_shares=r.weighted_shares,
            lifetime_claimed=r.lifetime_rewards_claimed,
        )
        return payout

    # -- views --------------------------------------------------------------

    def claimable(self, wallet: str) -> int:
        """Projected claimable share without mutating anything."""
        from .math import distribute as _distribute
        from .math import pending as _pending

        r = self.registrations.get(wallet)
        if r is None:
            return 0
        pool_acc = self.pool.acc_reward_per_share
        buffered = self.pool.undistributed
        if buffered and self.pool.total_weighted_shares:
            pool_acc, _ = _distribute(pool_acc, self.pool.total_weighted_shares, buffered)
        return r.pending_rewards + _pending(r.weighted_shares, pool_acc, r.reward_checkpoint)

    def assert_invariants(self, label: str = "") -> None:
        """Every invariant the program relies on, checked at once."""
        total_weight = sum(r.weighted_shares for r in self.registrations.values())
        assert self.pool.total_weighted_shares == total_weight, (
            f"{label}: pool weight {self.pool.total_weighted_shares} != sum {total_weight}"
        )

        paid = self.pool.total_claimed
        owed = sum(self.claimable(w) for w in self.registrations)
        assert paid + owed <= self.pool.total_collected, (
            f"{label}: pool insolvent - paid {paid} + owed {owed} > collected "
            f"{self.pool.total_collected}"
        )

        assert self.vault_lamports >= 0, f"{label}: deposit vault went negative"
        assert self.vault_lamports >= DEPOSIT_VAULT_RENT_LAMPORTS, (
            f"{label}: deposit vault {self.vault_lamports} fell below its own "
            f"rent-exemption {DEPOSIT_VAULT_RENT_LAMPORTS}"
        )

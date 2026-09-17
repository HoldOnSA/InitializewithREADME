"""An in-process mirror of the instruction handlers.

`Market` does what the on-chain program does, in the same order, against the
same state structs - including the lamport vault, the virtual reserves and the
holder count. Scenario tests read like real usage, and any divergence between
this and `programs/stackapp/src/instructions/` is a bug in one of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .constants import (
    DEFAULT_VIRTUAL_SOL_RESERVES,
    DEFAULT_VIRTUAL_TOKEN_RESERVES,
    MAX_VEST_DURATION_SECONDS,
)
from .logic import (
    StackError,
    claim_is_eligible,
    collect_tax,
    compact_oldest_lots,
    consume_spendable,
    prune_empty_lots,
    push_liquid_lot,
    push_vesting_lot,
    refresh_weight,
    release_vested,
    retained_through_maturity,
    touch_position,
)
from .math import (
    buy_cost_lamports,
    reputation_score_delta,
    sell_proceeds_lamports,
    spot_price_lamports,
    tier_for_score,
    validate_tax_curve,
)
from .state import ExitBreakdown, LoyaltyPool, Position, Reputation, TokenConfig


@dataclass
class Event:
    kind: str
    payload: dict


@dataclass
class Market:
    """One launch, plus every wallet's position and reputation."""

    config: TokenConfig = field(default_factory=TokenConfig)
    pool: LoyaltyPool = field(default_factory=LoyaltyPool)
    positions: Dict[str, Position] = field(default_factory=dict)
    reputations: Dict[str, Reputation] = field(default_factory=dict)

    now: int = 0
    slot: int = 0
    # Reserves at launch, so conservation checks have a baseline.
    initial_virtual_sol_reserves: int = DEFAULT_VIRTUAL_SOL_RESERVES
    initial_virtual_token_reserves: int = DEFAULT_VIRTUAL_TOKEN_RESERVES
    # Lamports actually sitting in the curve vault (excluding rent).
    vault_lamports: int = 0
    # Lamports each wallet has taken out, for round-trip accounting in tests.
    wallet_lamports: Dict[str, int] = field(default_factory=dict)
    events: List[Event] = field(default_factory=list)

    # -- setup --------------------------------------------------------------

    @classmethod
    def launch(
        cls,
        tax_curve: Sequence[tuple],
        vest_duration_seconds: int,
        mint: str = "MINT",
        creator: str = "creator",
        virtual_sol_reserves: int = 0,
        virtual_token_reserves: int = 0,
        decimals: int = 6,
        now: int = 0,
        slot: int = 0,
    ) -> "Market":
        validate_tax_curve(tax_curve)
        if not (0 <= vest_duration_seconds <= MAX_VEST_DURATION_SECONDS):
            raise StackError("InvalidVestDuration")

        config = TokenConfig(
            mint=mint,
            creator=creator,
            launch_timestamp=now,
            vest_duration_seconds=vest_duration_seconds,
            pool_account=f"pool:{mint}",
            curve_vault=f"curve_vault:{mint}",
            tax_curve=list(tax_curve),
            virtual_sol_reserves=virtual_sol_reserves or DEFAULT_VIRTUAL_SOL_RESERVES,
            virtual_token_reserves=virtual_token_reserves or DEFAULT_VIRTUAL_TOKEN_RESERVES,
            decimals=decimals,
        )
        m = cls(
            config=config,
            pool=LoyaltyPool(mint=mint),
            now=now,
            slot=slot,
            initial_virtual_sol_reserves=config.virtual_sol_reserves,
            initial_virtual_token_reserves=config.virtual_token_reserves,
        )
        m._emit("LaunchInitialized", mint=mint, creator=creator, vest=vest_duration_seconds)
        return m

    def _emit(self, _name: str, **payload) -> None:
        # `_name` is positional-by-convention so that payloads are free to carry
        # their own `kind` field (ExitExecuted does).
        payload.setdefault("timestamp", self.now)
        self.events.append(Event(_name, payload))

    def advance(self, seconds: int = 0, slots: int = 0) -> "Market":
        self.now += seconds
        # Devnet runs ~2.5 slots/second; default to that when only time moves.
        self.slot += slots if slots else max(seconds * 5 // 2, 0)
        return self

    def position(self, wallet: str) -> Position:
        p = self.positions.get(wallet)
        if p is None:
            p = Position(owner=wallet, mint=self.config.mint, first_buy_timestamp=self.now)
            self.positions[wallet] = p
        return p

    def reputation(self, wallet: str) -> Reputation:
        r = self.reputations.get(wallet)
        if r is None:
            r = Reputation(owner=wallet, first_seen_timestamp=self.now)
            self.reputations[wallet] = r
        return r

    # -- instructions -------------------------------------------------------

    def buy(self, wallet: str, amount: int, max_cost_lamports: int = 0) -> int:
        if amount <= 0:
            raise StackError("ZeroAmount")
        cost = buy_cost_lamports(
            self.config.virtual_sol_reserves, self.config.virtual_token_reserves, amount
        )
        if cost is None:
            raise StackError("CurveCapacityExceeded")
        if max_cost_lamports and cost > max_cost_lamports:
            raise StackError("SlippageExceeded")

        position = self.position(wallet)
        was_empty = position.total_remaining() == 0

        # Settle at the OLD weight before this buy's weight is added.
        touch_position(position, self.pool, self.now)
        push_vesting_lot(position, amount, self.now)
        if position.total_bought == 0:
            position.first_buy_timestamp = self.now
        position.total_bought += amount
        position.cost_basis_lamports += cost
        position.last_increase_slot = self.slot
        refresh_weight(position, self.pool, self.now)

        self.config.virtual_sol_reserves += cost
        self.config.virtual_token_reserves -= amount
        self.config.real_sol_reserves += cost
        self.config.tokens_sold += amount
        self.config.total_buy_volume_tokens += amount
        if was_empty:
            self.config.holder_count += 1

        self.vault_lamports += cost
        self.wallet_lamports[wallet] = self.wallet_lamports.get(wallet, 0) - cost

        self._emit(
            "BuyExecuted",
            mint=self.config.mint,
            buyer=wallet,
            amount=amount,
            cost_lamports=cost,
            slot=self.slot,
            spot_price_lamports=self.spot_price(),
        )
        return cost

    def claim_vested(self, wallet: str) -> int:
        position = self.position(wallet)
        touch_position(position, self.pool, self.now)
        released = release_vested(position, self.config.vest_duration_seconds, self.now)
        if released == 0:
            raise StackError("NothingToClaim")
        refresh_weight(position, self.pool, self.now)
        self._emit(
            "VestedClaimed",
            mint=self.config.mint,
            owner=wallet,
            released=released,
            spendable_total=position.spendable,
            locked_total=position.total_locked(),
        )
        return released

    def sell(self, wallet: str, amount: int, min_proceeds_lamports: int = 0) -> ExitBreakdown:
        position = self.position(wallet)
        touch_position(position, self.pool, self.now)
        out = consume_spendable(position, amount, self.config.tax_curve, self.now, False)

        proceeds = sell_proceeds_lamports(
            self.config.virtual_sol_reserves, self.config.virtual_token_reserves, out.net
        )
        if min_proceeds_lamports and proceeds < min_proceeds_lamports:
            raise StackError("SlippageExceeded")
        if proceeds > self.config.real_sol_reserves:
            raise StackError("CurveInsolvent")

        # Re-weight before distributing: no collecting on your own exit tax at
        # a weight you no longer hold.
        refresh_weight(position, self.pool, self.now)
        collect_tax(self.pool, out.tax)

        # Only the post-tax remainder goes back to the curve; the taxed portion
        # stays in circulation because the pool owns it now.
        self.config.virtual_sol_reserves -= proceeds
        self.config.virtual_token_reserves += out.net
        self.config.real_sol_reserves -= proceeds
        self.config.tokens_sold -= out.net
        self.config.total_sell_volume_tokens += amount
        if position.total_remaining() == 0:
            self.config.holder_count = max(self.config.holder_count - 1, 0)

        self.vault_lamports -= proceeds
        self.wallet_lamports[wallet] = self.wallet_lamports.get(wallet, 0) + proceeds

        self._emit(
            "ExitExecuted",
            mint=self.config.mint,
            owner=wallet,
            kind="sell",
            gross=out.gross,
            tax=out.tax,
            net=out.net,
            top_tax_bps=out.top_tax_bps,
            proceeds_lamports=proceeds,
        )
        if out.tax:
            self._emit(
                "TaxCollected",
                mint=self.config.mint,
                payer=wallet,
                amount=out.tax,
                acc_reward_per_share=self.pool.acc_reward_per_share,
                total_weighted_shares=self.pool.total_weighted_shares,
                undistributed=self.pool.undistributed,
            )
        out.proceeds_lamports = proceeds  # type: ignore[attr-defined]
        return out

    def transfer(self, sender: str, recipient: str, amount: int) -> ExitBreakdown:
        """Wallet-to-wallet. Taxed exactly like a sell; tenure does not travel."""
        if sender == recipient:
            raise StackError("SelfTransfer")
        if recipient == self.config.pool_account:
            raise StackError("SelfTransfer")

        from_position = self.position(sender)
        touch_position(from_position, self.pool, self.now)
        out = consume_spendable(from_position, amount, self.config.tax_curve, self.now, False)
        refresh_weight(from_position, self.pool, self.now)
        collect_tax(self.pool, out.tax)
        if from_position.total_remaining() == 0:
            self.config.holder_count = max(self.config.holder_count - 1, 0)

        to_position = self.position(recipient)
        was_empty = to_position.total_remaining() == 0
        touch_position(to_position, self.pool, self.now)
        # Freshly stamped: the tenure clock restarts in the new wallet.
        push_liquid_lot(to_position, out.net, self.now)
        to_position.total_bought += out.net
        to_position.last_increase_slot = self.slot
        refresh_weight(to_position, self.pool, self.now)
        if was_empty:
            self.config.holder_count += 1

        self._emit(
            "ExitExecuted",
            mint=self.config.mint,
            owner=sender,
            kind="transfer",
            gross=out.gross,
            tax=out.tax,
            net=out.net,
            top_tax_bps=out.top_tax_bps,
            destination=recipient,
        )
        if out.tax:
            self._emit(
                "TaxCollected",
                mint=self.config.mint,
                payer=sender,
                amount=out.tax,
                acc_reward_per_share=self.pool.acc_reward_per_share,
                total_weighted_shares=self.pool.total_weighted_shares,
                undistributed=self.pool.undistributed,
            )
        return out

    def donate_to_pool(self, wallet: str, amount: int) -> ExitBreakdown:
        """The one tax-exempt destination - and it forfeits the tokens."""
        position = self.position(wallet)
        touch_position(position, self.pool, self.now)
        out = consume_spendable(position, amount, self.config.tax_curve, self.now, True)
        refresh_weight(position, self.pool, self.now)
        collect_tax(self.pool, out.net)
        if position.total_remaining() == 0:
            self.config.holder_count = max(self.config.holder_count - 1, 0)
        self._emit(
            "ExitExecuted",
            mint=self.config.mint,
            owner=wallet,
            kind="donate",
            gross=out.gross,
            tax=0,
            net=out.net,
            destination=self.config.pool_account,
        )
        return out

    def claim_pool_share(self, wallet: str) -> int:
        position = self.position(wallet)
        if not claim_is_eligible(position, self.slot):
            raise StackError("ClaimTooSoon")

        touch_position(position, self.pool, self.now)
        payout = position.pending_rewards
        if payout == 0:
            raise StackError("NothingToClaim")

        position.pending_rewards = 0
        position.lifetime_rewards_claimed += payout
        self.pool.total_claimed += payout

        push_liquid_lot(position, payout, self.now)
        position.last_increase_slot = self.slot
        refresh_weight(position, self.pool, self.now)

        self._emit(
            "PoolClaimed",
            mint=self.config.mint,
            owner=wallet,
            amount=payout,
            weighted_shares=position.weighted_shares,
            lifetime_claimed=position.lifetime_rewards_claimed,
        )
        return payout

    def sync_weight(self, wallet: str) -> int:
        position = self.position(wallet)
        previous = position.weighted_shares
        touch_position(position, self.pool, self.now)
        refresh_weight(position, self.pool, self.now)
        self._emit(
            "WeightSynced",
            mint=self.config.mint,
            owner=wallet,
            previous_weight=previous,
            new_weight=position.weighted_shares,
            total_weighted_shares=self.pool.total_weighted_shares,
        )
        return position.weighted_shares

    def compact_lots(self, wallet: str) -> None:
        position = self.position(wallet)
        touch_position(position, self.pool, self.now)
        compact_oldest_lots(position)
        prune_empty_lots(position)
        refresh_weight(position, self.pool, self.now)
        self._emit("LotsCompacted", mint=self.config.mint, owner=wallet,
                   lots_remaining=len(position.lots))

    def update_reputation(self, wallet: str) -> int:
        position = self.position(wallet)
        touch_position(position, self.pool, self.now)
        refresh_weight(position, self.pool, self.now)

        if position.total_bought == 0:
            raise StackError("NotMatured")
        if not retained_through_maturity(position):
            raise StackError("ExitedBeforeMaturity")

        window_start = max(position.last_reputation_timestamp, position.first_buy_timestamp)
        window = self.now - window_start
        required = self.config.vest_duration_seconds or 86_400
        if window < required:
            raise StackError("NotMatured")

        capital = position.cost_basis_lamports
        score_delta = reputation_score_delta(capital, window)
        volume_delta = position.tenure_weighted_volume - position.credited_volume
        if score_delta == 0 and volume_delta == 0:
            raise StackError("ReputationAlreadyCredited")

        position.credited_volume = position.tenure_weighted_volume
        position.last_reputation_timestamp = self.now
        first_credit = position.maturity_credits == 0
        position.maturity_credits += 1

        rep = self.reputation(wallet)
        previous_tier = rep.tier
        rep.score += score_delta
        rep.total_tenure_weighted_volume += volume_delta
        if first_credit:
            rep.tokens_held_to_maturity += 1
        rep.tier = tier_for_score(rep.score)
        rep.last_update_timestamp = self.now

        self._emit(
            "ReputationUpdated",
            owner=wallet,
            mint=self.config.mint,
            score_delta=score_delta,
            score=rep.score,
            tier=rep.tier,
            capital_at_risk_lamports=capital,
            window_seconds=window,
        )
        if rep.tier > previous_tier:
            self._emit("TierUp", owner=wallet, previous_tier=previous_tier,
                       new_tier=rep.tier, score=rep.score)
        return score_delta

    # -- views --------------------------------------------------------------

    def spot_price(self) -> int:
        return spot_price_lamports(
            self.config.virtual_sol_reserves,
            self.config.virtual_token_reserves,
            self.config.decimals,
        )

    def claimable(self, wallet: str) -> int:
        """Projected claimable share without mutating anything."""
        from .math import pending as _pending

        position = self.positions.get(wallet)
        if position is None:
            return 0
        pool_acc = self.pool.acc_reward_per_share
        buffered = self.pool.undistributed
        if buffered and self.pool.total_weighted_shares:
            from .math import distribute as _distribute

            pool_acc, _ = _distribute(pool_acc, self.pool.total_weighted_shares, buffered)
        return position.pending_rewards + _pending(
            position.weighted_shares, pool_acc, position.reward_checkpoint
        )

    def total_outstanding(self) -> int:
        """Every token the ledger says exists, across all positions."""
        return sum(p.total_remaining() for p in self.positions.values())

    def assert_invariants(self, label: str = "") -> None:
        """Every invariant the program relies on, checked at once."""
        for wallet, p in self.positions.items():
            spendable = sum(lot.released for lot in p.lots)
            assert p.spendable == spendable, (
                f"{label}: {wallet} spendable {p.spendable} != sum(released) {spendable}"
            )
            for lot in p.lots:
                assert lot.cum_released <= lot.original, f"{label}: {wallet} cum_released > original"
                assert lot.released <= lot.original, f"{label}: {wallet} released > original"
                assert not lot.is_empty(), f"{label}: {wallet} has an unpruned empty lot"
            assert p.weighted_shares >= 0

        total_weight = sum(p.weighted_shares for p in self.positions.values())
        assert self.pool.total_weighted_shares == total_weight, (
            f"{label}: pool weight {self.pool.total_weighted_shares} != sum {total_weight}"
        )

        paid = self.pool.total_claimed
        owed = sum(self.claimable(w) for w in self.positions)
        assert paid + owed <= self.pool.total_collected, (
            f"{label}: pool insolvent - paid {paid} + owed {owed} > collected "
            f"{self.pool.total_collected}"
        )

        # The curve vault is solvent and matches the reserve it claims to hold.
        assert self.vault_lamports >= 0, f"{label}: curve vault went negative"
        assert self.vault_lamports == self.config.real_sol_reserves, (
            f"{label}: vault {self.vault_lamports} != real reserves "
            f"{self.config.real_sol_reserves}"
        )
        assert self.vault_lamports == (
            self.config.virtual_sol_reserves - self.initial_virtual_sol_reserves
        ), f"{label}: vault drifted from the virtual reserve delta"

        # Token conservation: every token the curve has sold is either sitting
        # in somebody's position or owed by the pool.
        sold = self.initial_virtual_token_reserves - self.config.virtual_token_reserves
        assert self.config.tokens_sold == sold, (
            f"{label}: tokens_sold {self.config.tokens_sold} != curve delta {sold}"
        )
        pool_held = self.pool.total_collected - self.pool.total_claimed
        assert self.config.tokens_sold == self.total_outstanding() + pool_held, (
            f"{label}: token conservation broken - sold {self.config.tokens_sold} != "
            f"outstanding {self.total_outstanding()} + pool-held {pool_held}"
        )

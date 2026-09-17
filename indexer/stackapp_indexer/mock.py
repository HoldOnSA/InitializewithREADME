"""Mock mode: the full API, driven by the simulator instead of devnet.

    python -m stackapp_indexer --mock

Serves exactly the same routes and WebSocket feed, but the data comes from
`stackapp_sim.Market` running a plausible market in the background. This exists
so the frontend can be built and demoed without a deployed program, an airdrop,
or a network connection - and because it is the same math the chain runs, the
shapes the UI sees are the shapes it will see on devnet.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import random
import time
from typing import Any, Dict, List, Optional

from stackapp_sim import TAX_CURVE_PRESETS, Market, StackError
from stackapp_sim.constants import DAY, MIN_CLAIM_DELAY_SLOTS

from .borsh import b58encode
from .config import Settings
from .store import Store

log = logging.getLogger("stackapp.mock")

WALLET_NAMES = [
    "diamond", "flipper", "whale", "steady", "latecomer", "burner", "crank", "patient",
]


def fake_pubkey(label: str) -> str:
    """A deterministic, well-formed base58 address for a label."""
    return b58encode(hashlib.sha256(f"stackapp:mock:{label}".encode()).digest())


class MockDriver:
    """Runs a simulated market and pushes its state into the store."""

    def __init__(self, store: Store, seed: int = 20260917):
        self.store = store
        self.rng = random.Random(seed)
        self.markets: List[Market] = []
        self.addresses: Dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None

    def address(self, label: str) -> str:
        if label not in self.addresses:
            self.addresses[label] = fake_pubkey(label)
        return self.addresses[label]

    # -- setup --------------------------------------------------------------

    def bootstrap(self) -> None:
        presets = ["diamond", "gentle", "brutal"]
        vests = [7 * DAY, 3 * DAY, 30 * DAY]
        # Anchor on the real clock. The store computes lot ages against
        # wall-clock time, so a hardcoded epoch would make every lot look
        # ancient and park the whole market in the top tenure tier.
        wall_clock = int(time.time())
        for i, (preset, vest) in enumerate(zip(presets, vests)):
            mint = self.address(f"mint-{preset}")
            market = Market.launch(
                TAX_CURVE_PRESETS[preset],
                vest_duration_seconds=vest,
                mint=mint,
                creator=self.address("creator"),
                now=wall_clock - (20 + 9 * i) * DAY,
                slot=100_000 * (i + 1),
            )
            self.markets.append(market)

            # Seed some history so the UI has something to show immediately.
            for step, wallet in enumerate(WALLET_NAMES[:5]):
                market.advance(seconds=6 * 3_600)
                with contextlib.suppress(StackError):
                    market.buy(self.address(wallet), self.rng.randint(1, 40) * 10**12)
                if step % 2 == 1:
                    market.advance(seconds=2 * DAY)
                    with contextlib.suppress(StackError):
                        market.claim_vested(self.address(wallet))
            market.advance(seconds=10 * DAY)
            for wallet in WALLET_NAMES[:3]:
                with contextlib.suppress(StackError):
                    market.claim_vested(self.address(wallet))
            with contextlib.suppress(StackError):
                position = market.position(self.address("flipper"))
                if position.spendable:
                    market.sell(self.address("flipper"), position.spendable // 2)

            self.sync(market)

        # Replay every market's backlog in global timestamp order. On devnet
        # events arrive in slot order, so the feed is chronological for free;
        # here three histories are built back to back, and replaying them in
        # build order would leave the feed interleaved and out of sequence.
        backlog = [
            (event.payload.get("timestamp", 0), index, market, event)
            for index, market in enumerate(self.markets)
            for event in market.events
        ]
        backlog.sort(key=lambda row: (row[0], row[1]))
        for _, _, market, event in backlog:
            self._apply_event(market, event)
        for market in self.markets:
            market.events.clear()

        # Align the clocks to the present. The launches stay staggered in the
        # past, but from here on there is a single "now" shared by every
        # market, as there would be on a single chain.
        now = max(int(time.time()), max(m.now for m in self.markets))
        slot = max(m.slot for m in self.markets)
        for market in self.markets:
            market.now = now
            market.slot = slot

        # Credit reputation where it has been earned, so /passport has
        # something to show on a cold start.
        for market in self.markets:
            for wallet in list(market.positions):
                with contextlib.suppress(StackError):
                    market.claim_vested(wallet)
                with contextlib.suppress(StackError):
                    market.update_reputation(wallet)
            self.sync(market)
            self.drain_events(market)

    # -- state -> store -----------------------------------------------------

    def sync(self, market: Market) -> None:
        config = market.config
        self.store.apply_account(
            "TokenConfig",
            {
                "mint": config.mint,
                "creator": config.creator,
                "launch_timestamp": config.launch_timestamp,
                "vest_duration_seconds": config.vest_duration_seconds,
                "pool_account": self.address(f"pool-{config.mint}"),
                "curve_vault": self.address(f"vault-{config.mint}"),
                "tax_curve": [
                    {"seconds_held": s, "tax_bps": b} for s, b in config.tax_curve
                ],
                "virtual_sol_reserves": config.virtual_sol_reserves,
                "virtual_token_reserves": config.virtual_token_reserves,
                "real_sol_reserves": config.real_sol_reserves,
                "tokens_sold": config.tokens_sold,
                "total_buy_volume_tokens": config.total_buy_volume_tokens,
                "total_sell_volume_tokens": config.total_sell_volume_tokens,
                "holder_count": config.holder_count,
                "decimals": config.decimals,
                "bump": 255,
                "curve_vault_bump": 254,
            },
            self.address(f"config-{config.mint}"),
            market.slot,
        )

        pool = market.pool
        self.store.apply_account(
            "LoyaltyPool",
            {
                "mint": config.mint,
                "acc_reward_per_share": pool.acc_reward_per_share,
                "total_weighted_shares": pool.total_weighted_shares,
                "total_collected": pool.total_collected,
                "total_claimed": pool.total_claimed,
                "undistributed": pool.undistributed,
                "bump": 255,
            },
            self.address(f"pool-{config.mint}"),
            market.slot,
        )

        for owner, position in market.positions.items():
            self.store.apply_account(
                "Position",
                {
                    "owner": owner,
                    "mint": config.mint,
                    "lots": [
                        {
                            "original": lot.original,
                            "cum_released": lot.cum_released,
                            "released": lot.released,
                            "buy_timestamp": lot.buy_timestamp,
                        }
                        for lot in position.lots
                    ],
                    "vested_claimed": position.vested_claimed,
                    "spendable": position.spendable,
                    "weighted_shares": position.weighted_shares,
                    "reward_checkpoint": position.reward_checkpoint,
                    "pending_rewards": position.pending_rewards,
                    "lifetime_rewards_claimed": position.lifetime_rewards_claimed,
                    "last_increase_slot": position.last_increase_slot,
                    "cost_basis_lamports": position.cost_basis_lamports,
                    "total_bought": position.total_bought,
                    "total_sold": position.total_sold,
                    "first_buy_timestamp": position.first_buy_timestamp,
                    "tenure_weighted_volume": position.tenure_weighted_volume,
                    "credited_volume": position.credited_volume,
                    "last_reputation_timestamp": position.last_reputation_timestamp,
                    "maturity_credits": position.maturity_credits,
                    "bump": 253,
                },
                self.address(f"position-{config.mint}-{owner}"),
                market.slot,
            )

        for owner, rep in market.reputations.items():
            self.store.apply_account(
                "Reputation",
                {
                    "owner": owner,
                    "score": rep.score,
                    "tier": rep.tier,
                    "tokens_held_to_maturity": rep.tokens_held_to_maturity,
                    "total_tenure_weighted_volume": rep.total_tenure_weighted_volume,
                    "first_seen_timestamp": rep.first_seen_timestamp,
                    "last_update_timestamp": rep.last_update_timestamp,
                    "bump": 252,
                },
                self.address(f"reputation-{owner}"),
                market.slot,
            )

    _EXIT_KINDS = {"sell": 0, "transfer": 1, "donate": 2}

    def _apply_event(self, market: Market, event) -> None:
        """Translate one simulator event into the on-chain event shape."""
        payload = dict(event.payload)
        if event.kind == "ExitExecuted":
            payload["kind_name"] = payload.get("kind", "sell")
            payload["kind"] = self._EXIT_KINDS.get(payload["kind_name"], 0)
            payload.setdefault("proceeds_lamports", 0)
            payload.setdefault("destination", "")
            payload.setdefault("top_tax_bps", 0)
        payload.setdefault("mint", market.config.mint)
        self.store.apply_event(
            event.kind,
            payload,
            slot=market.slot,
            signature=fake_pubkey(f"sig-{market.config.mint}-{len(self.store.feed)}"),
        )

    def drain_events(self, market: Market) -> None:
        """Move the simulator's events into the store's feed, then clear them."""
        for event in market.events:
            self._apply_event(market, event)
        market.events.clear()

    # -- the live loop ------------------------------------------------------

    async def run(self, tick_seconds: float = 3.0) -> None:
        self.bootstrap()
        log.info("mock market bootstrapped: %d launches", len(self.markets))
        while True:
            await asyncio.sleep(tick_seconds)
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - the demo must keep running
                log.warning("mock step failed: %s", exc)

    def step(self) -> None:
        # One shared clock across every launch: on devnet there is only one
        # chain, so the feed must never go backwards in time between mints.
        delta = self.rng.randint(60, 4 * 3_600)
        for other in self.markets:
            other.advance(seconds=delta)

        market = self.rng.choice(self.markets)
        wallet = self.address(self.rng.choice(WALLET_NAMES))
        position = market.position(wallet)
        action = self.rng.random()

        try:
            if action < 0.40:
                market.buy(wallet, self.rng.randint(1, 25) * 10**12)
            elif action < 0.60:
                market.claim_vested(wallet)
            elif action < 0.78:
                if position.spendable:
                    market.sell(wallet, self.rng.randint(1, position.spendable))
            elif action < 0.88:
                if position.spendable:
                    other = self.address(self.rng.choice(WALLET_NAMES))
                    if other != wallet:
                        market.transfer(wallet, other, self.rng.randint(1, position.spendable))
            elif action < 0.96:
                market.advance(slots=MIN_CLAIM_DELAY_SLOTS)
                market.claim_pool_share(wallet)
            else:
                market.update_reputation(wallet)
        except StackError as exc:
            log.debug("mock action rejected: %s", exc)

        market.assert_invariants("mock")
        self.sync(market)
        self.drain_events(market)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="stackapp-mock")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def health(self) -> Dict[str, Any]:
        return {
            "connected": True,
            "mode": "mock",
            "lastError": None,
            "markets": [m.config.mint for m in self.markets],
        }


def create_mock_app(settings: Optional[Settings] = None):
    """The real API surface, backed by the simulator."""
    from . import api as api_module

    settings = settings or Settings()
    store = Store()
    driver = MockDriver(store)

    app = api_module.create_app(settings=settings, store=store, mode="mock")

    # Replace the devnet subscriber with the mock driver. `create_app` only ever
    # calls `start`, `stop` and `health` on it, so the shapes line up.
    subscriber = app.state.subscriber

    async def _start() -> None:
        driver.start()

    subscriber.start = _start          # type: ignore[method-assign]
    subscriber.stop = driver.stop      # type: ignore[method-assign]
    subscriber.health = driver.health  # type: ignore[method-assign]
    app.state.driver = driver
    return app

"""Mock mode: the full API, driven by the simulator instead of devnet.

    python -m stackapp_indexer --mock

Serves exactly the same routes and WebSocket feed, but the data comes from
`stackapp_sim.Market` running a plausible set of registered tokens in the
background. This exists so the frontend can be built and demoed without a
deployed program, an airdrop, or a network connection - and because it is the
same math the chain runs, the shapes the UI sees are the shapes it will see
on devnet.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import random
import time
from typing import Any, Dict, List, Optional

from stackapp_sim import Market, StackError
from stackapp_sim.constants import MIN_CLAIM_DELAY_SLOTS

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
    """Runs several simulated markets and pushes their state into the store."""

    def __init__(self, store: Store, seed: int = 20260920):
        self.store = store
        self.rng = random.Random(seed)
        self.markets: List[Market] = []
        self.addresses: Dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None
        self._pending_id = 0

    def address(self, label: str) -> str:
        if label not in self.addresses:
            self.addresses[label] = fake_pubkey(label)
        return self.addresses[label]

    # -- setup --------------------------------------------------------------

    def bootstrap(self) -> None:
        presets = ["earlybird", "steady", "fresh"]
        # Anchor on the real clock so tenure tiers land where a viewer expects.
        wall_clock = int(time.time())
        for i, preset in enumerate(presets):
            mint = self.address(f"mint-{preset}")
            market = Market.register(
                mint=mint,
                creator=self.address("creator"),
                now=wall_clock - (40 - 10 * i) * 60,  # registered 10-40 min ago
                slot=100_000 * (i + 1),
            )
            self.markets.append(market)

            # Seed some history so the UI has something to show immediately.
            for wallet in WALLET_NAMES[:5]:
                market.write_registration(self.address(wallet))
                market.set_balance(self.address(wallet), self.rng.randint(1, 40) * 10**9)
                market.sync(self.address(wallet))

            market.donate(self.address("creator"), self.rng.randint(1, 20) * 10**7)
            market.advance(slots=MIN_CLAIM_DELAY_SLOTS)
            for wallet in WALLET_NAMES[:3]:
                with contextlib.suppress(StackError):
                    market.claim(self.address(wallet))

            self.sync(market)

        # Replay every market's backlog in global timestamp order.
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

        # A single shared "now" across every token, as there would be on one chain.
        now = max(int(time.time()), max(m.now for m in self.markets))
        slot = max(m.slot for m in self.markets)
        for market in self.markets:
            market.now = now
            market.slot = slot
            self.sync(market)

        # Seed a couple of pending (unwritten) registrations so the operator
        # queue has something to show.
        for wallet in ("latecomer", "burner"):
            self._pending_id += 1
            self.store.note_marker_candidate(
                self.markets[0].config.mint,
                self.address(wallet),
                2_500_000,
                self.markets[0].slot,
                fake_pubkey(f"marker-{self._pending_id}"),
            )

    # -- state -> store -----------------------------------------------------

    def sync(self, market: Market) -> None:
        config = market.config
        self.store.apply_account(
            "TokenConfig",
            {
                "mint": config.mint,
                "creator": config.creator,
                "registered_at": config.registered_at,
                "deposit_vault": config.deposit_vault,
                "deposit_bump": 254,
                "bump": 255,
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

        self.store.apply_account(
            "DepositVault",
            {"mint": config.mint, "bump": 254},
            config.deposit_vault,
            market.slot,
            lamports=market.vault_lamports,
        )

        for owner, r in market.registrations.items():
            self.store.apply_account(
                "Registration",
                {
                    "owner": owner,
                    "mint": config.mint,
                    "registered_at": r.registered_at,
                    "weighted_shares": r.weighted_shares,
                    "reward_checkpoint": r.reward_checkpoint,
                    "pending_rewards": r.pending_rewards,
                    "lifetime_rewards_claimed": r.lifetime_rewards_claimed,
                    "last_sync_slot": r.last_sync_slot,
                    "bump": 253,
                },
                self.address(f"registration-{config.mint}-{owner}"),
                market.slot,
            )

    def _apply_event(self, market: Market, event) -> None:
        payload = dict(event.payload)
        payload.setdefault("mint", market.config.mint)
        self.store.apply_event(
            event.kind,
            payload,
            slot=market.slot,
            signature=fake_pubkey(f"sig-{market.config.mint}-{len(self.store.feed)}"),
        )

    def drain_events(self, market: Market) -> None:
        for event in market.events:
            self._apply_event(market, event)
        market.events.clear()

    # -- the live loop ------------------------------------------------------

    async def run(self, tick_seconds: float = 3.0) -> None:
        self.bootstrap()
        log.info("mock market bootstrapped: %d registered tokens", len(self.markets))
        while True:
            await asyncio.sleep(tick_seconds)
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - the demo must keep running
                log.warning("mock step failed: %s", exc)

    def step(self) -> None:
        # One shared clock across every token: on devnet there is only one
        # chain, so the feed must never go backwards in time between mints.
        delta = self.rng.randint(10, 300)
        for other in self.markets:
            other.advance(seconds=delta, slots=delta * 2)

        market = self.rng.choice(self.markets)
        wallet = self.address(self.rng.choice(WALLET_NAMES))
        action = self.rng.random()

        try:
            if action < 0.30:
                if wallet not in market.registrations:
                    market.write_registration(wallet)
                    market.set_balance(wallet, self.rng.randint(1, 25) * 10**9)
            elif action < 0.60:
                if wallet in market.registrations:
                    market.set_balance(
                        wallet, max(0, market.balance_of(wallet) + self.rng.randint(-5, 10) * 10**8)
                    )
                    market.sync(wallet)
            elif action < 0.85:
                if wallet in market.registrations:
                    with contextlib.suppress(StackError):
                        market.claim(wallet)
            else:
                market.donate(self.address("creator"), self.rng.randint(1, 10) * 10**7)
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

"""The queryable view the frontend reads.

Everything lives in memory: this is a prototype indexer, and a restart simply
re-subscribes and backfills. Swapping the dicts for Postgres would be a
contained change - `Store` is the only thing the API layer talks to.

Derived numbers (tenure multiplier, projected claimable share) are computed
with `stackapp_sim`, the same Python mirror of the on-chain math that the
test suite uses, so the UI and the program agree by construction.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

from stackapp_sim.constants import MIN_CLAIM_DELAY_SLOTS, REGISTRATION_MARKER_LAMPORTS
from stackapp_sim.math import distribute, pending, tenure_multiplier_bps

MAX_FEED_EVENTS = 5_000


@dataclass
class FeedEvent:
    name: str
    data: Dict[str, Any]
    slot: int
    signature: str
    received_at: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "data": self.data,
            "slot": self.slot,
            "signature": self.signature,
            "receivedAt": self.received_at,
        }


@dataclass
class PendingRegistration:
    """A marker-transfer candidate the indexer noticed but hasn't been
    written on chain yet - the operator (holding `GlobalConfig.authority`)
    reviews and signs `write_registration` for these, they are never
    auto-signed. See `SECURITY_NOTES.md`."""

    mint: str
    owner: str
    amount: int
    slot: int
    signature: str
    detected_at: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mint": self.mint,
            "owner": self.owner,
            "amount": self.amount,
            "slot": self.slot,
            "signature": self.signature,
            "detectedAt": self.detected_at,
        }


@dataclass
class Store:
    global_config: Optional[Dict[str, Any]] = None
    configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    vault_lamports: Dict[str, int] = field(default_factory=dict)  # by mint
    # keyed by (mint, owner)
    registrations: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)

    # Marker-transfer candidates awaiting a human `write_registration`,
    # keyed by (mint, owner) so a repeat marker doesn't queue twice.
    pending_registrations: Dict[Tuple[str, str], PendingRegistration] = field(default_factory=dict)

    feed: Deque[FeedEvent] = field(default_factory=lambda: deque(maxlen=MAX_FEED_EVENTS))
    subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = field(default_factory=set)

    current_slot: int = 0
    last_event_timestamp: int = 0

    # -- ingestion ----------------------------------------------------------

    def apply_account(
        self, name: str, data: Dict[str, Any], address: str, slot: int, lamports: int = 0
    ) -> None:
        """Upsert a decoded program account."""
        self.current_slot = max(self.current_slot, slot)
        record = dict(data)
        record["_address"] = address
        record["_slot"] = slot

        if name == "GlobalConfig":
            self.global_config = record
        elif name == "TokenConfig":
            self.configs[data["mint"]] = record
        elif name == "LoyaltyPool":
            self.pools[data["mint"]] = record
        elif name == "DepositVault":
            self.vault_lamports[data["mint"]] = lamports
        elif name == "Registration":
            key = (data["mint"], data["owner"])
            self.registrations[key] = record
            # A written registration retires any matching candidate.
            self.pending_registrations.pop(key, None)

    def note_marker_candidate(
        self, mint: str, owner: str, amount: int, slot: int, signature: str
    ) -> None:
        """Record a plain SOL transfer into a token's DepositVault that
        matches `REGISTRATION_MARKER_LAMPORTS`. Only a candidate - see
        `PendingRegistration`."""
        key = (mint, owner)
        if key in self.registrations:
            return  # already written
        self.pending_registrations[key] = PendingRegistration(
            mint=mint,
            owner=owner,
            amount=amount,
            slot=slot,
            signature=signature,
            detected_at=time.time(),
        )

    def apply_event(
        self, name: str, data: Dict[str, Any], slot: int = 0, signature: str = ""
    ) -> FeedEvent:
        """Record an event and fan it out to WebSocket subscribers."""
        self.current_slot = max(self.current_slot, slot)
        if isinstance(data.get("timestamp"), int):
            self.last_event_timestamp = max(self.last_event_timestamp, data["timestamp"])

        event = FeedEvent(
            name=name,
            data=data,
            slot=slot,
            signature=signature,
            received_at=time.time(),
        )
        self.feed.appendleft(event)
        self._broadcast({"type": "event", "event": event.as_dict()})
        return event

    def _broadcast(self, message: Dict[str, Any]) -> None:
        dead = []
        for queue in self.subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self.subscribers.discard(queue)

    def subscribe(self, maxsize: int = 256) -> "asyncio.Queue[Dict[str, Any]]":
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=maxsize)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        self.subscribers.discard(queue)

    # -- clock --------------------------------------------------------------

    def now(self) -> int:
        """Best guess at the on-chain clock."""
        return max(int(time.time()), self.last_event_timestamp)

    # -- views --------------------------------------------------------------

    def list_tokens(self) -> List[Dict[str, Any]]:
        return [self.token_view(mint) for mint in self.configs]

    def token_view(self, mint: str) -> Optional[Dict[str, Any]]:
        config = self.configs.get(mint)
        if config is None:
            return None
        now = self.now()

        return {
            "mint": mint,
            "creator": config["creator"],
            "registeredAt": config["registered_at"],
            "ageSeconds": now - config["registered_at"],
            "depositVault": config["deposit_vault"],
            "vaultLamports": self.vault_lamports.get(mint, 0),
            "registrationMarkerLamports": REGISTRATION_MARKER_LAMPORTS,
            "pool": self.pool_view(mint),
            "registrationCount": sum(1 for (m, _o) in self.registrations if m == mint),
            "pendingRegistrationCount": sum(
                1 for (m, _o) in self.pending_registrations if m == mint
            ),
        }

    def pool_view(self, mint: str) -> Dict[str, Any]:
        pool = self.pools.get(mint)
        if pool is None:
            return {
                "totalCollected": 0,
                "totalClaimed": 0,
                "undistributed": 0,
                "outstanding": 0,
                "totalWeightedShares": 0,
                "accRewardPerShare": "0",
            }
        return {
            "totalCollected": pool["total_collected"],
            "totalClaimed": pool["total_claimed"],
            "undistributed": pool["undistributed"],
            "outstanding": pool["total_collected"] - pool["total_claimed"],
            "totalWeightedShares": pool["total_weighted_shares"],
            # u128 is beyond JSON's safe integer range, so it goes as a string.
            "accRewardPerShare": str(pool["acc_reward_per_share"]),
        }

    def _effective_acc(self, mint: str) -> int:
        """Accumulator including any buffered fee that would flush on the
        next touch - so the UI's projection matches what a claim would
        actually pay."""
        pool = self.pools.get(mint)
        if pool is None:
            return 0
        acc = pool["acc_reward_per_share"]
        if pool["undistributed"] and pool["total_weighted_shares"]:
            acc, _ = distribute(acc, pool["total_weighted_shares"], pool["undistributed"])
        return acc

    def registration_view(self, mint: str, owner: str) -> Optional[Dict[str, Any]]:
        r = self.registrations.get((mint, owner))
        if r is None:
            return None
        now = self.now()
        age = now - r["registered_at"]

        claimable = r["pending_rewards"] + pending(
            r["weighted_shares"], self._effective_acc(mint), r["reward_checkpoint"]
        )
        eligible_slot = r["last_sync_slot"] + MIN_CLAIM_DELAY_SLOTS

        pool = self.pools.get(mint, {})
        total_weight = pool.get("total_weighted_shares", 0)

        return {
            "mint": mint,
            "owner": owner,
            "registeredAt": r["registered_at"],
            "ageSeconds": age,
            "tenureMultiplierBps": tenure_multiplier_bps(age),
            "weightedShares": r["weighted_shares"],
            "poolSharePpm": (
                r["weighted_shares"] * 1_000_000 // total_weight if total_weight else 0
            ),
            "projectedClaimable": claimable,
            "lifetimeRewardsClaimed": r["lifetime_rewards_claimed"],
            "claimEligibleAtSlot": eligible_slot,
            "claimEligible": self.current_slot >= eligible_slot,
        }

    def registrations_for_owner(self, owner: str) -> List[Dict[str, Any]]:
        return [
            view
            for (mint, o) in list(self.registrations)
            if o == owner
            for view in [self.registration_view(mint, owner)]
            if view is not None
        ]

    def registrations_for_mint(self, mint: str) -> List[Dict[str, Any]]:
        return [
            view
            for (m, owner) in list(self.registrations)
            if m == mint
            for view in [self.registration_view(mint, owner)]
            if view is not None
        ]

    def pending_registrations_for_mint(self, mint: str) -> List[Dict[str, Any]]:
        return [p.as_dict() for (m, _o), p in self.pending_registrations.items() if m == mint]

    def feed_view(
        self,
        limit: int = 100,
        mint: Optional[str] = None,
        kinds: Optional[Iterable[str]] = None,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        wanted = set(kinds) if kinds else None
        out: List[Dict[str, Any]] = []
        for event in self.feed:  # already newest-first
            if wanted and event.name not in wanted:
                continue
            if mint and event.data.get("mint") != mint:
                continue
            if owner and owner != event.data.get("owner"):
                continue
            out.append(event.as_dict())
            if len(out) >= limit:
                break
        return out

    def stats(self) -> Dict[str, Any]:
        return {
            "tokens": len(self.configs),
            "registrations": len(self.registrations),
            "pendingRegistrations": len(self.pending_registrations),
            "events": len(self.feed),
            "currentSlot": self.current_slot,
            "subscribers": len(self.subscribers),
            "totalFeesCollected": sum(p["total_collected"] for p in self.pools.values()),
        }

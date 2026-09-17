"""The queryable view the frontend reads.

Everything lives in memory: this is a prototype indexer, and a restart simply
re-subscribes and backfills. Swapping the dicts for Postgres would be a
contained change - `Store` is the only thing the API layer talks to.

Derived numbers (vesting %, current tax rate, projected claimable share) are
computed with `stackapp_sim`, the same Python mirror of the on-chain math that
the test suite uses, so the UI and the program agree by construction.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

from stackapp_sim.constants import MIN_CLAIM_DELAY_SLOTS, TIER_NAMES
from stackapp_sim.math import (
    distribute,
    pending,
    spot_price_lamports,
    tax_bps_for_age,
    tenure_multiplier_bps,
    tier_for_score,
    vested_amount,
    vested_bps,
)

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
class Store:
    configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # keyed by (mint, owner)
    positions: Dict[Tuple[str, str], Dict[str, Any]] = field(default_factory=dict)
    reputations: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    feed: Deque[FeedEvent] = field(default_factory=lambda: deque(maxlen=MAX_FEED_EVENTS))
    subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = field(default_factory=set)

    current_slot: int = 0
    last_event_timestamp: int = 0

    # -- ingestion ----------------------------------------------------------

    def apply_account(self, name: str, data: Dict[str, Any], address: str, slot: int) -> None:
        """Upsert a decoded program account."""
        self.current_slot = max(self.current_slot, slot)
        record = dict(data)
        record["_address"] = address
        record["_slot"] = slot

        if name == "TokenConfig":
            self.configs[data["mint"]] = record
        elif name == "LoyaltyPool":
            self.pools[data["mint"]] = record
        elif name == "Position":
            self.positions[(data["mint"], data["owner"])] = record
        elif name == "Reputation":
            self.reputations[data["owner"]] = record

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
        pool = self.pools.get(mint, {})
        now = self.now()
        curve = [(p["seconds_held"], p["tax_bps"]) for p in config["tax_curve"]]

        return {
            "mint": mint,
            "creator": config["creator"],
            "launchTimestamp": config["launch_timestamp"],
            "ageSeconds": now - config["launch_timestamp"],
            "vestDurationSeconds": config["vest_duration_seconds"],
            "decimals": config["decimals"],
            "taxCurve": [
                {"secondsHeld": secs, "taxBps": bps} for secs, bps in curve
            ],
            "currentOpeningTaxBps": tax_bps_for_age(curve, 0),
            "curve": {
                "virtualSolReserves": config["virtual_sol_reserves"],
                "virtualTokenReserves": config["virtual_token_reserves"],
                "realSolReserves": config["real_sol_reserves"],
                "tokensSold": config["tokens_sold"],
                "spotPriceLamports": spot_price_lamports(
                    config["virtual_sol_reserves"],
                    config["virtual_token_reserves"],
                    config["decimals"],
                ),
                "marketCapLamports": spot_price_lamports(
                    config["virtual_sol_reserves"],
                    config["virtual_token_reserves"],
                    config["decimals"],
                )
                * (config["tokens_sold"] // (10 ** config["decimals"]))
                if config["decimals"] <= 18
                else 0,
            },
            "pool": self.pool_view(mint),
            "holderCount": config["holder_count"],
            "totalBuyVolumeTokens": config["total_buy_volume_tokens"],
            "totalSellVolumeTokens": config["total_sell_volume_tokens"],
            "poolAccount": config["pool_account"],
            "curveVault": config["curve_vault"],
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
        """Accumulator including any buffered tax that would flush on the next
        touch - so the UI's projection matches what a claim would actually pay."""
        pool = self.pools.get(mint)
        if pool is None:
            return 0
        acc = pool["acc_reward_per_share"]
        if pool["undistributed"] and pool["total_weighted_shares"]:
            acc, _ = distribute(acc, pool["total_weighted_shares"], pool["undistributed"])
        return acc

    def position_view(self, mint: str, owner: str) -> Optional[Dict[str, Any]]:
        position = self.positions.get((mint, owner))
        if position is None:
            return None
        config = self.configs.get(mint)
        vest = config["vest_duration_seconds"] if config else 0
        curve = (
            [(p["seconds_held"], p["tax_bps"]) for p in config["tax_curve"]] if config else []
        )
        now = self.now()

        lots = []
        total_remaining = 0
        total_locked = 0
        for lot in position["lots"]:
            locked = max(lot["original"] - lot["cum_released"], 0)
            remaining = locked + lot["released"]
            age = now - lot["buy_timestamp"]
            total_remaining += remaining
            total_locked += locked
            lots.append(
                {
                    "original": lot["original"],
                    "remaining": remaining,
                    "locked": locked,
                    "released": lot["released"],
                    "buyTimestamp": lot["buy_timestamp"],
                    "ageSeconds": age,
                    "vestedBps": vested_bps(lot["buy_timestamp"], now, vest),
                    "claimableNow": max(
                        vested_amount(lot["original"], lot["buy_timestamp"], now, vest)
                        - lot["cum_released"],
                        0,
                    ),
                    "currentTaxBps": tax_bps_for_age(curve, age),
                    "tenureMultiplierBps": tenure_multiplier_bps(age),
                }
            )

        claimable = position["pending_rewards"] + pending(
            position["weighted_shares"], self._effective_acc(mint), position["reward_checkpoint"]
        )
        eligible_slot = position["last_increase_slot"] + MIN_CLAIM_DELAY_SLOTS

        pool = self.pools.get(mint, {})
        total_weight = pool.get("total_weighted_shares", 0)

        return {
            "mint": mint,
            "owner": owner,
            "lots": lots,
            "totalRemaining": total_remaining,
            "totalLocked": total_locked,
            "spendable": position["spendable"],
            "vestedClaimed": position["vested_claimed"],
            "claimableVestedNow": sum(lot["claimableNow"] for lot in lots),
            "unlockProgressBps": (
                ((total_remaining - total_locked) * 10_000 // total_remaining)
                if total_remaining
                else 10_000
            ),
            "weightedShares": position["weighted_shares"],
            "poolSharePpm": (
                position["weighted_shares"] * 1_000_000 // total_weight if total_weight else 0
            ),
            "projectedClaimable": claimable,
            "lifetimeRewardsClaimed": position["lifetime_rewards_claimed"],
            "claimEligibleAtSlot": eligible_slot,
            "claimEligible": self.current_slot >= eligible_slot,
            "costBasisLamports": position["cost_basis_lamports"],
            "totalBought": position["total_bought"],
            "totalSold": position["total_sold"],
            "firstBuyTimestamp": position["first_buy_timestamp"],
            "averageHoldSeconds": (
                sum(lot["ageSeconds"] * lot["remaining"] for lot in lots) // total_remaining
                if total_remaining
                else 0
            ),
        }

    def positions_for_owner(self, owner: str) -> List[Dict[str, Any]]:
        return [
            view
            for (mint, holder) in list(self.positions)
            if holder == owner
            for view in [self.position_view(mint, owner)]
            if view is not None
        ]

    def positions_for_mint(self, mint: str) -> List[Dict[str, Any]]:
        return [
            view
            for (m, owner) in list(self.positions)
            if m == mint
            for view in [self.position_view(mint, owner)]
            if view is not None
        ]

    def reputation_view(self, owner: str) -> Dict[str, Any]:
        rep = self.reputations.get(owner)
        positions = self.positions_for_owner(owner)

        score = rep["score"] if rep else 0
        tier = rep["tier"] if rep else tier_for_score(score)
        held_positions = [p for p in positions if p["totalRemaining"] > 0]
        weighted_age = sum(p["averageHoldSeconds"] * p["totalRemaining"] for p in held_positions)
        weight_base = sum(p["totalRemaining"] for p in held_positions)

        return {
            "owner": owner,
            "score": str(score),
            "tier": tier,
            "tierName": TIER_NAMES[min(tier, len(TIER_NAMES) - 1)],
            "nextTierAt": _next_tier_threshold(score),
            "tokensHeldToMaturity": rep["tokens_held_to_maturity"] if rep else 0,
            "totalTenureWeightedVolume": str(
                rep["total_tenure_weighted_volume"] if rep else 0
            ),
            "firstSeenTimestamp": rep["first_seen_timestamp"] if rep else 0,
            "lastUpdateTimestamp": rep["last_update_timestamp"] if rep else 0,
            "activePositions": len(held_positions),
            "averageHoldSeconds": weighted_age // weight_base if weight_base else 0,
            "capitalAtRiskLamports": sum(p["costBasisLamports"] for p in positions),
            "perks": tier_perks(tier),
            "positions": positions,
        }

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
            if owner and owner not in (
                event.data.get("owner"),
                event.data.get("buyer"),
                event.data.get("payer"),
                event.data.get("destination"),
            ):
                continue
            out.append(event.as_dict())
            if len(out) >= limit:
                break
        return out

    def stats(self) -> Dict[str, Any]:
        return {
            "tokens": len(self.configs),
            "positions": len(self.positions),
            "reputations": len(self.reputations),
            "events": len(self.feed),
            "currentSlot": self.current_slot,
            "subscribers": len(self.subscribers),
            "totalTaxCollected": sum(p["total_collected"] for p in self.pools.values()),
        }


def _next_tier_threshold(score: int) -> Optional[str]:
    from stackapp_sim.constants import REPUTATION_TIER_THRESHOLDS

    for threshold in REPUTATION_TIER_THRESHOLDS:
        if score < threshold:
            return str(threshold)
    return None


def tier_perks(tier: int) -> List[str]:
    """What a tier unlocks. Prototype copy - none of this is enforced on chain
    beyond the tier number itself, which is the point of showing it plainly."""
    ladder = [
        ["Read-only access to the public feed"],
        ["Launch fee rebate", "Feed badge"],
        ["Early access to new launches (1h)", "Reduced launch fee"],
        ["Early access to new launches (6h)", "Creator allowlist eligibility"],
        ["Early access to new launches (24h)", "Governance weight on curve presets"],
    ]
    return ladder[min(tier, len(ladder) - 1)]

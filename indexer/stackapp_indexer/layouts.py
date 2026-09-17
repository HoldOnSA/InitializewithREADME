"""Borsh layouts and Anchor discriminators for the StackApp program.

These mirror `programs/stackapp/src/state/` and `events.rs` field for field and
in order - borsh is positional, so order is the schema.

Anchor derives its 8-byte discriminators by hashing a namespaced name:

    account:  sha256("account:<StructName>")[:8]
    event:    sha256("event:<EventName>")[:8]
    ix:       sha256("global:<snake_case_name>")[:8]

so the indexer can decode without a generated IDL. If `target/idl/stackapp.json`
exists after `anchor build`, `load_idl_overrides()` will cross-check against it.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .borsh import Reader, decode_struct

Layout = Sequence[Tuple[str, Any]]

DISCRIMINATOR_LEN = 8


def discriminator(namespace: str, name: str) -> bytes:
    return hashlib.sha256(f"{namespace}:{name}".encode()).digest()[:DISCRIMINATOR_LEN]


# ---------------------------------------------------------------------------
# Shared sub-structs
# ---------------------------------------------------------------------------

TAX_POINT: Layout = [
    ("seconds_held", "i64"),
    ("tax_bps", "u16"),
]

LOT: Layout = [
    ("original", "u64"),
    ("cum_released", "u64"),
    ("released", "u64"),
    ("buy_timestamp", "i64"),
]

# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

TOKEN_CONFIG: Layout = [
    ("mint", "pubkey"),
    ("creator", "pubkey"),
    ("launch_timestamp", "i64"),
    ("vest_duration_seconds", "i64"),
    ("pool_account", "pubkey"),
    ("curve_vault", "pubkey"),
    ("tax_curve", ("vec", ("struct", TAX_POINT))),
    ("virtual_sol_reserves", "u64"),
    ("virtual_token_reserves", "u64"),
    ("real_sol_reserves", "u64"),
    ("tokens_sold", "u64"),
    ("total_buy_volume_tokens", "u64"),
    ("total_sell_volume_tokens", "u64"),
    ("holder_count", "u32"),
    ("decimals", "u8"),
    ("bump", "u8"),
    ("curve_vault_bump", "u8"),
]

POSITION: Layout = [
    ("owner", "pubkey"),
    ("mint", "pubkey"),
    ("lots", ("vec", ("struct", LOT))),
    ("vested_claimed", "u64"),
    ("spendable", "u64"),
    ("weighted_shares", "u64"),
    ("reward_checkpoint", "u128"),
    ("pending_rewards", "u64"),
    ("lifetime_rewards_claimed", "u64"),
    ("last_increase_slot", "u64"),
    ("cost_basis_lamports", "u64"),
    ("total_bought", "u64"),
    ("total_sold", "u64"),
    ("first_buy_timestamp", "i64"),
    ("tenure_weighted_volume", "u128"),
    ("credited_volume", "u128"),
    ("last_reputation_timestamp", "i64"),
    ("maturity_credits", "u32"),
    ("bump", "u8"),
]

LOYALTY_POOL: Layout = [
    ("mint", "pubkey"),
    ("acc_reward_per_share", "u128"),
    ("total_weighted_shares", "u64"),
    ("total_collected", "u64"),
    ("total_claimed", "u64"),
    ("undistributed", "u64"),
    ("bump", "u8"),
]

REPUTATION: Layout = [
    ("owner", "pubkey"),
    ("score", "u128"),
    ("tier", "u8"),
    ("tokens_held_to_maturity", "u32"),
    ("total_tenure_weighted_volume", "u128"),
    ("first_seen_timestamp", "i64"),
    ("last_update_timestamp", "i64"),
    ("bump", "u8"),
]

CURVE_VAULT: Layout = [
    ("mint", "pubkey"),
    ("bump", "u8"),
]

ACCOUNT_LAYOUTS: Dict[str, Layout] = {
    "TokenConfig": TOKEN_CONFIG,
    "Position": POSITION,
    "LoyaltyPool": LOYALTY_POOL,
    "Reputation": REPUTATION,
    "CurveVault": CURVE_VAULT,
}

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

LAUNCH_INITIALIZED: Layout = [
    ("mint", "pubkey"),
    ("creator", "pubkey"),
    ("launch_timestamp", "i64"),
    ("vest_duration_seconds", "i64"),
    ("virtual_sol_reserves", "u64"),
    ("virtual_token_reserves", "u64"),
    ("tax_curve_points", "u8"),
    ("opening_tax_bps", "u16"),
    ("floor_tax_bps", "u16"),
]

BUY_EXECUTED: Layout = [
    ("mint", "pubkey"),
    ("buyer", "pubkey"),
    ("amount", "u64"),
    ("cost_lamports", "u64"),
    ("timestamp", "i64"),
    ("slot", "u64"),
    ("position_total", "u64"),
    ("spot_price_lamports", "u64"),
    ("tokens_sold", "u64"),
]

VESTED_CLAIMED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("released", "u64"),
    ("spendable_total", "u64"),
    ("locked_total", "u64"),
    ("timestamp", "i64"),
]

EXIT_EXECUTED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("kind", "u8"),
    ("gross", "u64"),
    ("tax", "u64"),
    ("net", "u64"),
    ("top_tax_bps", "u16"),
    ("proceeds_lamports", "u64"),
    ("destination", "pubkey"),
    ("timestamp", "i64"),
]

TAX_COLLECTED: Layout = [
    ("mint", "pubkey"),
    ("payer", "pubkey"),
    ("amount", "u64"),
    ("acc_reward_per_share", "u128"),
    ("total_weighted_shares", "u64"),
    ("pool_total_collected", "u64"),
    ("undistributed", "u64"),
    ("timestamp", "i64"),
]

POOL_CLAIMED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("amount", "u64"),
    ("weighted_shares", "u64"),
    ("lifetime_claimed", "u64"),
    ("timestamp", "i64"),
]

WEIGHT_SYNCED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("previous_weight", "u64"),
    ("new_weight", "u64"),
    ("total_weighted_shares", "u64"),
    ("timestamp", "i64"),
]

REPUTATION_UPDATED: Layout = [
    ("owner", "pubkey"),
    ("mint", "pubkey"),
    ("score_delta", "u128"),
    ("score", "u128"),
    ("tier", "u8"),
    ("tokens_held_to_maturity", "u32"),
    ("total_tenure_weighted_volume", "u128"),
    ("capital_at_risk_lamports", "u64"),
    ("window_seconds", "i64"),
    ("timestamp", "i64"),
]

TIER_UP: Layout = [
    ("owner", "pubkey"),
    ("previous_tier", "u8"),
    ("new_tier", "u8"),
    ("score", "u128"),
    ("timestamp", "i64"),
]

LOTS_COMPACTED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("lots_remaining", "u8"),
    ("merged_timestamp", "i64"),
]

EVENT_LAYOUTS: Dict[str, Layout] = {
    "LaunchInitialized": LAUNCH_INITIALIZED,
    "BuyExecuted": BUY_EXECUTED,
    "VestedClaimed": VESTED_CLAIMED,
    "ExitExecuted": EXIT_EXECUTED,
    "TaxCollected": TAX_COLLECTED,
    "PoolClaimed": POOL_CLAIMED,
    "WeightSynced": WEIGHT_SYNCED,
    "ReputationUpdated": REPUTATION_UPDATED,
    "TierUp": TIER_UP,
    "LotsCompacted": LOTS_COMPACTED,
}

# `ExitExecuted.kind` values, mirroring events.rs.
EXIT_KINDS = {0: "sell", 1: "transfer", 2: "donate"}

EVENT_BY_DISCRIMINATOR: Dict[bytes, str] = {
    discriminator("event", name): name for name in EVENT_LAYOUTS
}
ACCOUNT_BY_DISCRIMINATOR: Dict[bytes, str] = {
    discriminator("account", name): name for name in ACCOUNT_LAYOUTS
}


def decode_event(payload: bytes) -> Optional[Dict[str, Any]]:
    """Decode one `Program data:` payload. Returns None if unrecognised."""
    if len(payload) < DISCRIMINATOR_LEN:
        return None
    name = EVENT_BY_DISCRIMINATOR.get(payload[:DISCRIMINATOR_LEN])
    if name is None:
        return None
    data = decode_struct(EVENT_LAYOUTS[name], payload, DISCRIMINATOR_LEN)
    if name == "ExitExecuted":
        data["kind_name"] = EXIT_KINDS.get(data["kind"], "unknown")
    return {"name": name, "data": data}


def decode_account(data: bytes) -> Optional[Dict[str, Any]]:
    """Decode a program-owned account. Returns None if unrecognised."""
    if len(data) < DISCRIMINATOR_LEN:
        return None
    name = ACCOUNT_BY_DISCRIMINATOR.get(data[:DISCRIMINATOR_LEN])
    if name is None:
        return None
    return {"name": name, "data": decode_struct(ACCOUNT_LAYOUTS[name], data, DISCRIMINATOR_LEN)}


def encode_event(name: str, data: Dict[str, Any]) -> bytes:
    """Produce what the program would log. Used by tests and the mock feed."""
    from .borsh import encode_struct

    if name not in EVENT_LAYOUTS:
        raise KeyError(f"unknown event {name!r}")
    return discriminator("event", name) + encode_struct(EVENT_LAYOUTS[name], data)


def encode_account(name: str, data: Dict[str, Any]) -> bytes:
    from .borsh import encode_struct

    if name not in ACCOUNT_LAYOUTS:
        raise KeyError(f"unknown account {name!r}")
    return discriminator("account", name) + encode_struct(ACCOUNT_LAYOUTS[name], data)


def load_idl_overrides(idl_path: pathlib.Path) -> List[str]:
    """Cross-check these layouts against a generated IDL.

    Returns a list of human-readable mismatches - empty means the hand-written
    layouts agree with what `anchor build` produced. The indexer logs these at
    startup rather than failing, so a partially-updated checkout still runs.
    """
    if not idl_path.is_file():
        return [f"no IDL at {idl_path} (run `anchor build`); using built-in layouts"]

    idl = json.loads(idl_path.read_text(encoding="utf-8"))
    problems: List[str] = []

    # Anchor 0.30 puts struct definitions under `types`, with accounts/events
    # referencing them by name.
    types = {t["name"]: t for t in idl.get("types", [])}

    def idl_fields(name: str) -> Optional[List[str]]:
        entry = types.get(name)
        if not entry:
            return None
        kind = entry.get("type", {})
        if kind.get("kind") != "struct":
            return None
        return [f["name"] for f in kind.get("fields", [])]

    for group, layouts in (("account", ACCOUNT_LAYOUTS), ("event", EVENT_LAYOUTS)):
        for name, layout in layouts.items():
            expected = [field for field, _ in layout]
            actual = idl_fields(name)
            if actual is None:
                problems.append(f"{group} {name}: not present in the IDL")
            elif actual != expected:
                problems.append(f"{group} {name}: IDL fields {actual} != layout {expected}")

    return problems


def parse_program_data_lines(logs: Sequence[str]) -> List[Dict[str, Any]]:
    """Pull every decodable Anchor event out of a transaction's log lines."""
    import base64

    found: List[Dict[str, Any]] = []
    for line in logs:
        for prefix in ("Program data: ", "Program log: "):
            if not line.startswith(prefix):
                continue
            blob = line[len(prefix) :].strip()
            try:
                payload = base64.b64decode(blob, validate=True)
            except Exception:
                continue
            event = decode_event(payload)
            if event is not None:
                found.append(event)
            break
    return found


__all__ = [
    "ACCOUNT_LAYOUTS",
    "EVENT_LAYOUTS",
    "EXIT_KINDS",
    "decode_account",
    "decode_event",
    "encode_account",
    "encode_event",
    "discriminator",
    "load_idl_overrides",
    "parse_program_data_lines",
    "Reader",
]

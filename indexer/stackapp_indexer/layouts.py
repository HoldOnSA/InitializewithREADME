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
# Accounts
# ---------------------------------------------------------------------------

GLOBAL_CONFIG: Layout = [
    ("authority", "pubkey"),
    ("bump", "u8"),
]

TOKEN_CONFIG: Layout = [
    ("mint", "pubkey"),
    ("creator", "pubkey"),
    ("registered_at", "i64"),
    ("deposit_vault", "pubkey"),
    ("deposit_bump", "u8"),
    ("bump", "u8"),
]

DEPOSIT_VAULT: Layout = [
    ("mint", "pubkey"),
    ("total_marker_deposits", "u64"),
    ("total_rent_spent", "u64"),
    ("bump", "u8"),
]

REGISTRATION: Layout = [
    ("owner", "pubkey"),
    ("mint", "pubkey"),
    ("registered_at", "i64"),
    ("weighted_shares", "u64"),
    ("reward_checkpoint", "u128"),
    ("pending_rewards", "u64"),
    ("lifetime_rewards_claimed", "u64"),
    ("last_sync_slot", "u64"),
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

ACCOUNT_LAYOUTS: Dict[str, Layout] = {
    "GlobalConfig": GLOBAL_CONFIG,
    "TokenConfig": TOKEN_CONFIG,
    "DepositVault": DEPOSIT_VAULT,
    "Registration": REGISTRATION,
    "LoyaltyPool": LOYALTY_POOL,
}

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

MINT_REGISTERED: Layout = [
    ("mint", "pubkey"),
    ("creator", "pubkey"),
    ("deposit_vault", "pubkey"),
    ("registered_at", "i64"),
]

WALLET_REGISTERED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("registered_at", "i64"),
]

WEIGHT_SYNCED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("balance", "u64"),
    ("previous_weight", "u64"),
    ("new_weight", "u64"),
    ("total_weighted_shares", "u64"),
    ("timestamp", "i64"),
]

FEE_COLLECTED: Layout = [
    ("mint", "pubkey"),
    ("amount", "u64"),
    ("acc_reward_per_share", "u128"),
    ("total_weighted_shares", "u64"),
    ("total_collected", "u64"),
    ("undistributed", "u64"),
    ("timestamp", "i64"),
]

REWARD_CLAIMED: Layout = [
    ("mint", "pubkey"),
    ("owner", "pubkey"),
    ("amount", "u64"),
    ("weighted_shares", "u64"),
    ("lifetime_claimed", "u64"),
    ("timestamp", "i64"),
]

EVENT_LAYOUTS: Dict[str, Layout] = {
    "MintRegistered": MINT_REGISTERED,
    "WalletRegistered": WALLET_REGISTERED,
    "WeightSynced": WEIGHT_SYNCED,
    "FeeCollected": FEE_COLLECTED,
    "RewardClaimed": REWARD_CLAIMED,
}

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
    return {"name": name, "data": decode_struct(EVENT_LAYOUTS[name], payload, DISCRIMINATOR_LEN)}


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
    "decode_account",
    "decode_event",
    "encode_account",
    "encode_event",
    "discriminator",
    "load_idl_overrides",
    "parse_program_data_lines",
    "Reader",
]

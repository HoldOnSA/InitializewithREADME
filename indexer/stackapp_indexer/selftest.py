"""Dependency-free decode self-test.

Every layout is filled with boundary-ish values, encoded, and decoded again.
This is what catches a field added to the Rust struct but not mirrored here -
borsh is positional, so a missing field silently shifts everything after it.

    python -m stackapp_indexer --check-layouts
"""

from __future__ import annotations

from typing import Any, Dict, Sequence, Tuple

from .borsh import b58encode
from .layouts import (
    ACCOUNT_LAYOUTS,
    EVENT_LAYOUTS,
    decode_account,
    decode_event,
    encode_account,
    encode_event,
)

_SCALAR_SAMPLES: Dict[str, Any] = {
    "u8": 255,
    "u16": 9_000,
    "u32": 4_000_000_000,
    "u64": 2**64 - 1,
    "u128": 2**127 + 7,
    "i8": -128,
    "i16": -32_768,
    "i32": -2_147_483_648,
    "i64": -9_223_372_036_854_775_808,
    "i128": -(2**126) - 3,
    "bool": True,
    "string": "stackapp",
}


def sample(kind, salt: int = 1) -> Any:
    if isinstance(kind, str):
        if kind == "pubkey":
            return b58encode(bytes((salt * 7 + i) % 256 for i in range(32)))
        if kind == "bytes32":
            return bytes((salt + i) % 256 for i in range(32))
        if kind not in _SCALAR_SAMPLES:
            raise KeyError(f"no sample for {kind!r}")
        return _SCALAR_SAMPLES[kind]

    tag = kind[0]
    if tag == "vec":
        return [sample(kind[1], salt + i) for i in range(3)]
    if tag == "array":
        return [sample(kind[1], salt + i) for i in range(kind[2])]
    if tag == "struct":
        return sample_struct(kind[1], salt)
    if tag == "option":
        return sample(kind[1], salt)
    raise KeyError(f"no sample for {kind!r}")


def sample_struct(layout: Sequence[Tuple[str, Any]], salt: int = 1) -> Dict[str, Any]:
    return {name: sample(kind, salt + i) for i, (name, kind) in enumerate(layout)}


def round_trip_events() -> list:
    """Returns a list of failure strings; empty means everything matched."""
    failures = []
    for name, layout in EVENT_LAYOUTS.items():
        expected = sample_struct(layout)
        decoded = decode_event(encode_event(name, expected))
        if decoded is None:
            failures.append(f"event {name}: discriminator did not match")
            continue
        if decoded["name"] != name:
            failures.append(f"event {name}: decoded as {decoded['name']}")
            continue
        actual = {k: v for k, v in decoded["data"].items() if k in expected}
        if actual != expected:
            for key in expected:
                if actual.get(key) != expected[key]:
                    failures.append(
                        f"event {name}.{key}: {actual.get(key)!r} != {expected[key]!r}"
                    )
    return failures


def round_trip_accounts() -> list:
    failures = []
    for name, layout in ACCOUNT_LAYOUTS.items():
        expected = sample_struct(layout)
        decoded = decode_account(encode_account(name, expected))
        if decoded is None:
            failures.append(f"account {name}: discriminator did not match")
            continue
        if decoded["name"] != name:
            failures.append(f"account {name}: decoded as {decoded['name']}")
            continue
        if decoded["data"] != expected:
            for key in expected:
                if decoded["data"].get(key) != expected[key]:
                    failures.append(
                        f"account {name}.{key}: "
                        f"{decoded['data'].get(key)!r} != {expected[key]!r}"
                    )
    return failures


def check_layouts() -> int:
    failures = round_trip_events() + round_trip_accounts()
    if failures:
        print(f"{len(failures)} layout problem(s):")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print(
        f"OK: {len(EVENT_LAYOUTS)} events and {len(ACCOUNT_LAYOUTS)} accounts "
        "round-tripped cleanly"
    )
    return 0

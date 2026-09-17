"""Program-derived address helpers, stdlib only.

The indexer needs to name the PDAs it is watching (and the API hands them to
the frontend so the wallet adapter can build instructions). That needs
`find_program_address`, which needs an ed25519 on-curve test - about thirty
lines of modular arithmetic, so there is no reason to take a dependency for it.
"""

from __future__ import annotations

import hashlib
from typing import Sequence, Tuple

from .borsh import b58decode, b58encode
from stackapp_sim.constants import (
    SEED_CONFIG,
    SEED_CURVE_VAULT,
    SEED_POOL,
    SEED_POSITION,
    SEED_REPUTATION,
)

_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_PDA_MARKER = b"ProgramDerivedAddress"
MAX_SEED_LENGTH = 32


class PdaError(ValueError):
    pass


def is_on_curve(point: bytes) -> bool:
    """Is this 32-byte value a valid ed25519 point (i.e. a real public key)?

    PDAs are, by definition, the addresses that are *not*.
    """
    if len(point) != 32:
        return False
    y = int.from_bytes(point, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        return False

    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    xx = u * pow(v, _P - 2, _P) % _P

    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    if (x * x - xx) % _P != 0:
        return False
    if x == 0 and sign:
        return False
    return True


def create_program_address(seeds: Sequence[bytes], program_id: str) -> str:
    for seed in seeds:
        if len(seed) > MAX_SEED_LENGTH:
            raise PdaError(f"seed longer than {MAX_SEED_LENGTH} bytes: {seed!r}")
    digest = hashlib.sha256(
        b"".join(seeds) + b58decode(program_id) + _PDA_MARKER
    ).digest()
    if is_on_curve(digest):
        raise PdaError("derived address is on the curve")
    return b58encode(digest)


def find_program_address(seeds: Sequence[bytes], program_id: str) -> Tuple[str, int]:
    for bump in range(255, -1, -1):
        try:
            return create_program_address([*seeds, bytes([bump])], program_id), bump
        except PdaError:
            continue
    raise PdaError("no off-curve address found")


# -- the program's own PDAs -------------------------------------------------


def token_config_pda(mint: str, program_id: str) -> Tuple[str, int]:
    return find_program_address([SEED_CONFIG, b58decode(mint)], program_id)


def loyalty_pool_pda(mint: str, program_id: str) -> Tuple[str, int]:
    return find_program_address([SEED_POOL, b58decode(mint)], program_id)


def position_pda(mint: str, owner: str, program_id: str) -> Tuple[str, int]:
    return find_program_address(
        [SEED_POSITION, b58decode(mint), b58decode(owner)], program_id
    )


def reputation_pda(owner: str, program_id: str) -> Tuple[str, int]:
    return find_program_address([SEED_REPUTATION, b58decode(owner)], program_id)


def curve_vault_pda(mint: str, program_id: str) -> Tuple[str, int]:
    return find_program_address([SEED_CURVE_VAULT, b58decode(mint)], program_id)


__all__ = [
    "is_on_curve",
    "create_program_address",
    "find_program_address",
    "token_config_pda",
    "loyalty_pool_pda",
    "position_pda",
    "reputation_pda",
    "curve_vault_pda",
    "PdaError",
]

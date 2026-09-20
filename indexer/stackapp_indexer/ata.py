"""Mirror of `programs/stackapp/src/ata.rs`.

Real, live pump.fun mints split roughly 14:1 Token-2022 vs legacy SPL Token
(verified against sampled mainnet tokens) - `sync`/`claim` need the caller's
ATA derived under whichever program actually owns the mint, not assumed.
"""

from __future__ import annotations

from .pda import find_program_address

ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

SUPPORTED_TOKEN_PROGRAMS = (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID)


def derive_ata(owner: str, mint: str, token_program: str) -> str:
    from .borsh import b58decode

    address, _ = find_program_address(
        [b58decode(owner), b58decode(token_program), b58decode(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return address


__all__ = [
    "ASSOCIATED_TOKEN_PROGRAM_ID",
    "TOKEN_PROGRAM_ID",
    "TOKEN_2022_PROGRAM_ID",
    "SUPPORTED_TOKEN_PROGRAMS",
    "derive_ata",
]

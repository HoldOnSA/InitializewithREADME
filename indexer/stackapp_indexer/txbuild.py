"""Build unsigned Solana transactions in Python.

This is what lets the whole prototype run without Node. The server assembles a
legacy transaction message - account ordering, shortvec lengths, Anchor
instruction data - base58-encodes it, and hands it to the browser. The wallet
extension signs and submits it:

    window.solana.request({
      method: "signAndSendTransaction",
      params: { message: "<base58 message>" },
    })

**No private key ever reaches this process.** The server builds an unsigned
message and never sees a signature; signing and submission happen entirely in
the user's wallet - including for `register_mint`/`write_registration`, which
are authority-gated on chain but signed the same way as everything else here:
whichever wallet the operator connects. If it isn't the real
`GlobalConfig.authority`, the transaction simply fails on chain, the same as
any other constraint violation this module doesn't pre-check.

Message layout (legacy):

    header            3 bytes: required signatures, readonly signed, readonly unsigned
    account_keys      shortvec length + 32 bytes each
    recent_blockhash  32 bytes
    instructions      shortvec count, then for each:
                        program_id_index  u8
                        accounts          shortvec length + u8 indices
                        data              shortvec length + bytes

Account keys are ordered writable-signers, readonly-signers, writable-nonsigners,
readonly-nonsigners, with the fee payer forced first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .ata import derive_ata
from .borsh import Writer, b58decode, b58encode
from .layouts import discriminator
from .pda import (
    deposit_vault_pda,
    global_config_pda,
    loyalty_pool_pda,
    registration_pda,
    token_config_pda,
)

SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"


class TxBuildError(ValueError):
    pass


def compact_u16(value: int) -> bytes:
    """Solana shortvec length prefix."""
    if value < 0 or value > 0xFFFF:
        raise TxBuildError(f"length out of range: {value}")
    out = bytearray()
    while True:
        elem = value & 0x7F
        value >>= 7
        if value == 0:
            out.append(elem)
            return bytes(out)
        out.append(elem | 0x80)


@dataclass(frozen=True)
class AccountMeta:
    pubkey: str
    is_signer: bool = False
    is_writable: bool = False


def signer(pubkey: str, is_writable: bool = True) -> AccountMeta:
    return AccountMeta(pubkey, True, is_writable)


def rw(pubkey: str) -> AccountMeta:
    return AccountMeta(pubkey, False, True)


def ro(pubkey: str) -> AccountMeta:
    return AccountMeta(pubkey, False, False)


@dataclass
class Instruction:
    program_id: str
    accounts: List[AccountMeta] = field(default_factory=list)
    data: bytes = b""


def build_message(
    fee_payer: str, instructions: Sequence[Instruction], recent_blockhash: str
) -> bytes:
    """Serialize a legacy transaction message."""
    if not instructions:
        raise TxBuildError("a transaction needs at least one instruction")

    # Merge duplicate accounts, taking the union of their flags.
    merged: Dict[str, AccountMeta] = {}

    def note(meta: AccountMeta) -> None:
        existing = merged.get(meta.pubkey)
        if existing is None:
            merged[meta.pubkey] = meta
        else:
            merged[meta.pubkey] = AccountMeta(
                meta.pubkey,
                existing.is_signer or meta.is_signer,
                existing.is_writable or meta.is_writable,
            )

    # The fee payer is always the first account, and always a writable signer.
    note(AccountMeta(fee_payer, True, True))
    for instruction in instructions:
        for meta in instruction.accounts:
            note(meta)
        # Program ids ride along as readonly non-signers.
        note(AccountMeta(instruction.program_id, False, False))

    payer = merged.pop(fee_payer)
    rest = list(merged.values())

    def bucket(meta: AccountMeta) -> int:
        if meta.is_signer:
            return 0 if meta.is_writable else 1
        return 2 if meta.is_writable else 3

    # Stable sort: bucket order, then insertion order within a bucket.
    ordered = [payer] + sorted(rest, key=bucket)

    num_required_signatures = sum(1 for m in ordered if m.is_signer)
    num_readonly_signed = sum(1 for m in ordered if m.is_signer and not m.is_writable)
    num_readonly_unsigned = sum(
        1 for m in ordered if not m.is_signer and not m.is_writable
    )

    index_of = {meta.pubkey: i for i, meta in enumerate(ordered)}

    out = bytearray()
    out += bytes([num_required_signatures, num_readonly_signed, num_readonly_unsigned])
    out += compact_u16(len(ordered))
    for meta in ordered:
        raw = b58decode(meta.pubkey)
        if len(raw) != 32:
            raise TxBuildError(f"not a 32-byte address: {meta.pubkey}")
        out += raw

    blockhash = b58decode(recent_blockhash)
    if len(blockhash) != 32:
        raise TxBuildError("recent_blockhash must be 32 bytes")
    out += blockhash

    out += compact_u16(len(instructions))
    for instruction in instructions:
        out += bytes([index_of[instruction.program_id]])
        out += compact_u16(len(instruction.accounts))
        out += bytes(index_of[meta.pubkey] for meta in instruction.accounts)
        out += compact_u16(len(instruction.data))
        out += instruction.data

    return bytes(out)


def build_message_b58(
    fee_payer: str, instructions: Sequence[Instruction], recent_blockhash: str
) -> str:
    """What the browser wallet expects."""
    return b58encode(build_message(fee_payer, instructions, recent_blockhash))


# ---------------------------------------------------------------------------
# Instruction builders
# ---------------------------------------------------------------------------
#
# Account order must match each `#[derive(Accounts)]` struct field order in
# `programs/stackapp/src/instructions/` exactly. `tests/test_txbuild.py`
# cross-checks these against the Rust source.


def _ix(program_id: str, accounts: List[AccountMeta], name: str, data: bytes = b"") -> Instruction:
    return Instruction(program_id, accounts, discriminator("global", name) + data)


def initialize_config(program_id: str, payer: str, authority: str) -> Instruction:
    global_config, _ = global_config_pda(program_id)
    data = Writer().write("pubkey", authority).bytes()
    return _ix(
        program_id,
        [signer(payer), rw(global_config), ro(SYSTEM_PROGRAM_ID)],
        "initialize_config",
        data,
    )


def update_authority(program_id: str, authority: str, new_authority: str) -> Instruction:
    global_config, _ = global_config_pda(program_id)
    data = Writer().write("pubkey", new_authority).bytes()
    return _ix(
        program_id,
        [signer(authority, False), rw(global_config)],
        "update_authority",
        data,
    )


def register_mint(program_id: str, authority: str, mint: str, creator: str) -> Instruction:
    global_config, _ = global_config_pda(program_id)
    token_config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    vault, _ = deposit_vault_pda(mint, program_id)

    data = Writer().write("pubkey", creator).bytes()
    return _ix(
        program_id,
        [
            signer(authority),
            ro(global_config),
            ro(mint),
            rw(token_config),
            rw(pool),
            rw(vault),
            ro(SYSTEM_PROGRAM_ID),
        ],
        "register_mint",
        data,
    )


def write_registration(program_id: str, authority: str, owner: str, mint: str) -> Instruction:
    global_config, _ = global_config_pda(program_id)
    token_config, _ = token_config_pda(mint, program_id)
    vault, _ = deposit_vault_pda(mint, program_id)
    registration, _ = registration_pda(mint, owner, program_id)

    return _ix(
        program_id,
        [
            signer(authority, False),
            ro(global_config),
            ro(owner),
            ro(mint),
            ro(token_config),
            rw(vault),
            rw(registration),
            ro(SYSTEM_PROGRAM_ID),
        ],
        "write_registration",
    )


def sync(program_id: str, cranker: str, owner: str, mint: str, token_program: str) -> Instruction:
    token_config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    registration, _ = registration_pda(mint, owner, program_id)
    holder_token_account = derive_ata(owner, mint, token_program)

    return _ix(
        program_id,
        [
            signer(cranker, False),
            ro(mint),
            ro(owner),
            ro(token_config),
            rw(pool),
            rw(registration),
            ro(holder_token_account),
        ],
        "sync",
    )


def claim(program_id: str, owner: str, mint: str, token_program: str) -> Instruction:
    token_config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    registration, _ = registration_pda(mint, owner, program_id)
    vault, _ = deposit_vault_pda(mint, program_id)
    holder_token_account = derive_ata(owner, mint, token_program)

    return _ix(
        program_id,
        [
            signer(owner),
            ro(mint),
            ro(token_config),
            rw(pool),
            rw(registration),
            rw(vault),
            ro(holder_token_account),
        ],
        "claim",
    )


def donate(program_id: str, donor: str, mint: str, amount: int) -> Instruction:
    pool, _ = loyalty_pool_pda(mint, program_id)
    vault, _ = deposit_vault_pda(mint, program_id)

    data = Writer().write("u64", int(amount)).bytes()
    return _ix(
        program_id,
        [signer(donor), ro(mint), rw(pool), rw(vault), ro(SYSTEM_PROGRAM_ID)],
        "donate",
        data,
    )


BUILDERS = {
    "initialize_config": initialize_config,
    "update_authority": update_authority,
    "register_mint": register_mint,
    "write_registration": write_registration,
    "sync": sync,
    "claim": claim,
    "donate": donate,
}


async def latest_blockhash(rpc_http: str) -> str:
    """Fetch a recent blockhash for the message."""
    import httpx

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getLatestBlockhash",
        "params": [{"commitment": "confirmed"}],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(rpc_http, json=payload)
        response.raise_for_status()
        body = response.json()
    if "error" in body:
        raise TxBuildError(f"getLatestBlockhash failed: {body['error']}")
    return body["result"]["value"]["blockhash"]


async def mint_token_program(rpc_http: str, mint: str) -> str:
    """Which token program owns `mint` - Legacy Token or Token-2022.

    Real pump.fun mints split roughly 14:1 Token-2022 vs legacy, so this is
    never assumed; `sync`/`claim` derive the caller's ATA under whichever one
    actually owns the mint.
    """
    import httpx

    from .ata import SUPPORTED_TOKEN_PROGRAMS

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [mint, {"encoding": "base64"}],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(rpc_http, json=payload)
        response.raise_for_status()
        body = response.json()
    if "error" in body:
        raise TxBuildError(f"getAccountInfo failed: {body['error']}")
    value = (body.get("result") or {}).get("value")
    if value is None:
        raise TxBuildError(f"mint {mint} does not exist on chain")
    owner = value["owner"]
    if owner not in SUPPORTED_TOKEN_PROGRAMS:
        raise TxBuildError(f"mint {mint} is owned by an unsupported token program {owner}")
    return owner

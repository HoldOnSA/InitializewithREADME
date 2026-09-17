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
the user's wallet.

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

from .borsh import Writer, b58decode, b58encode
from .layouts import discriminator
from .pda import (
    curve_vault_pda,
    loyalty_pool_pda,
    position_pda,
    reputation_pda,
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


def initialize_launch(
    program_id: str,
    creator: str,
    mint: str,
    tax_curve: Sequence[tuple],
    vest_duration_seconds: int,
    virtual_sol_reserves: int = 0,
    virtual_token_reserves: int = 0,
    decimals: int = 6,
) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    vault, _ = curve_vault_pda(mint, program_id)

    writer = Writer()
    writer.write(("vec", ("struct", [("seconds_held", "i64"), ("tax_bps", "u16")])),
                 [{"seconds_held": int(s), "tax_bps": int(b)} for s, b in tax_curve])
    writer.write("i64", int(vest_duration_seconds))
    writer.write("u64", int(virtual_sol_reserves))
    writer.write("u64", int(virtual_token_reserves))
    writer.write("u8", int(decimals))

    return _ix(
        program_id,
        [signer(creator), ro(mint), rw(config), rw(pool), rw(vault), ro(SYSTEM_PROGRAM_ID)],
        "initialize_launch",
        writer.bytes(),
    )


def buy(
    program_id: str, buyer: str, mint: str, amount: int, max_cost_lamports: int = 0
) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, buyer, program_id)
    vault, _ = curve_vault_pda(mint, program_id)

    data = Writer().write("u64", int(amount)).write("u64", int(max_cost_lamports)).bytes()
    return _ix(
        program_id,
        [
            signer(buyer),
            ro(mint),
            rw(config),
            rw(pool),
            rw(position),
            rw(vault),
            ro(SYSTEM_PROGRAM_ID),
        ],
        "buy",
        data,
    )


def claim_vested(program_id: str, owner: str, mint: str) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, owner, program_id)
    return _ix(
        program_id,
        [signer(owner, False), ro(mint), ro(config), rw(pool), rw(position)],
        "claim_vested",
    )


def sell(
    program_id: str, seller: str, mint: str, amount: int, min_proceeds_lamports: int = 0
) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, seller, program_id)
    vault, _ = curve_vault_pda(mint, program_id)

    data = Writer().write("u64", int(amount)).write("u64", int(min_proceeds_lamports)).bytes()
    return _ix(
        program_id,
        [
            signer(seller),
            ro(mint),
            rw(config),
            rw(pool),
            rw(position),
            rw(vault),
            ro(SYSTEM_PROGRAM_ID),
        ],
        "sell",
        data,
    )


def transfer_position(
    program_id: str, sender: str, recipient: str, mint: str, amount: int
) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    from_position, _ = position_pda(mint, sender, program_id)
    to_position, _ = position_pda(mint, recipient, program_id)

    data = Writer().write("u64", int(amount)).bytes()
    return _ix(
        program_id,
        [
            signer(sender),
            ro(mint),
            ro(recipient),
            rw(config),
            rw(pool),
            rw(from_position),
            rw(to_position),
            ro(SYSTEM_PROGRAM_ID),
        ],
        "transfer_position",
        data,
    )


def donate_to_pool(program_id: str, owner: str, mint: str, amount: int) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, owner, program_id)

    data = Writer().write("u64", int(amount)).bytes()
    return _ix(
        program_id,
        [signer(owner, False), ro(mint), rw(config), rw(pool), rw(position)],
        "donate_to_pool",
        data,
    )


def claim_pool_share(program_id: str, owner: str, mint: str) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, owner, program_id)
    return _ix(
        program_id,
        [signer(owner, False), ro(mint), ro(config), rw(pool), rw(position)],
        "claim_pool_share",
    )


def update_reputation(program_id: str, owner: str, mint: str) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, owner, program_id)
    reputation, _ = reputation_pda(owner, program_id)
    return _ix(
        program_id,
        [
            signer(owner),
            ro(mint),
            ro(config),
            rw(pool),
            rw(position),
            rw(reputation),
            ro(SYSTEM_PROGRAM_ID),
        ],
        "update_reputation",
    )


def sync_weight(program_id: str, cranker: str, owner: str, mint: str) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, owner, program_id)
    return _ix(
        program_id,
        [
            signer(cranker, False),
            ro(mint),
            ro(owner),
            ro(config),
            rw(pool),
            rw(position),
        ],
        "sync_weight",
    )


def compact_lots(program_id: str, owner: str, mint: str) -> Instruction:
    config, _ = token_config_pda(mint, program_id)
    pool, _ = loyalty_pool_pda(mint, program_id)
    position, _ = position_pda(mint, owner, program_id)
    return _ix(
        program_id,
        [signer(owner, False), ro(mint), ro(config), rw(pool), rw(position)],
        "compact_lots",
    )


BUILDERS = {
    "initialize_launch": initialize_launch,
    "buy": buy,
    "claim_vested": claim_vested,
    "sell": sell,
    "transfer_position": transfer_position,
    "donate_to_pool": donate_to_pool,
    "claim_pool_share": claim_pool_share,
    "update_reputation": update_reputation,
    "sync_weight": sync_weight,
    "compact_lots": compact_lots,
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

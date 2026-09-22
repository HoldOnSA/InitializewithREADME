"""Transaction assembly, and parity with the Rust account structs.

Account order and mutability in `txbuild.py` must match each
`#[derive(Accounts)]` struct field for field. Borsh and Solana messages are
positional, so a mismatch is silent until it fails on chain -
`test_account_shape_matches_rust` parses the Rust and checks.
"""

import pathlib
import re
import unittest

from stackapp_indexer.ata import TOKEN_PROGRAM_ID
from stackapp_indexer.borsh import b58decode, b58encode
from stackapp_indexer.layouts import discriminator
from stackapp_indexer import txbuild
from stackapp_indexer.txbuild import (
    AccountMeta,
    Instruction,
    TxBuildError,
    build_message,
    build_message_b58,
    compact_u16,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
INSTRUCTIONS_DIR = REPO / "programs" / "stackapp" / "src" / "instructions"

PROGRAM_ID = "Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS"
SYSTEM = "11111111111111111111111111111111"
BLOCKHASH = "EETubP5AKHgjPAhzPAFcb8BAY1hMH639CWCFTqi3hq1k"


# Deterministic stand-in addresses.
def key(label: str) -> str:
    import hashlib

    return b58encode(hashlib.sha256(label.encode()).digest())


ALICE = key("alice")
BOB = key("bob")
MINT = key("mint")


class TestCompactU16(unittest.TestCase):
    def test_known_encodings(self):
        self.assertEqual(compact_u16(0), b"\x00")
        self.assertEqual(compact_u16(1), b"\x01")
        self.assertEqual(compact_u16(127), b"\x7f")
        self.assertEqual(compact_u16(128), b"\x80\x01")
        self.assertEqual(compact_u16(255), b"\xff\x01")
        self.assertEqual(compact_u16(16_383), b"\xff\x7f")
        self.assertEqual(compact_u16(16_384), b"\x80\x80\x01")

    def test_out_of_range_is_rejected(self):
        with self.assertRaises(TxBuildError):
            compact_u16(-1)
        with self.assertRaises(TxBuildError):
            compact_u16(70_000)


class TestMessage(unittest.TestCase):
    def simple(self):
        return Instruction(
            PROGRAM_ID,
            [
                AccountMeta(ALICE, True, True),
                AccountMeta(BOB, False, True),
                AccountMeta(MINT, False, False),
            ],
            b"\x01\x02\x03",
        )

    def test_header_counts(self):
        message = build_message(ALICE, [self.simple()], BLOCKHASH)
        required, readonly_signed, readonly_unsigned = message[0], message[1], message[2]
        self.assertEqual(required, 1, "only the fee payer signs")
        self.assertEqual(readonly_signed, 0)
        # mint + program id are readonly non-signers
        self.assertEqual(readonly_unsigned, 2)

    def test_fee_payer_is_the_first_account(self):
        message = build_message(ALICE, [self.simple()], BLOCKHASH)
        offset = 3 + len(compact_u16(4))
        self.assertEqual(message[offset : offset + 32], b58decode(ALICE))

    def test_accounts_are_ordered_by_privilege(self):
        message = build_message(ALICE, [self.simple()], BLOCKHASH)
        keys = self.parse_keys(message)
        # writable signer, then writable non-signer, then the readonly ones
        self.assertEqual(keys[0], ALICE)
        self.assertEqual(keys[1], BOB)
        self.assertEqual(set(keys[2:]), {MINT, PROGRAM_ID})

    def test_duplicate_accounts_are_merged_with_union_of_flags(self):
        # BOB appears readonly in one instruction and writable in another.
        first = Instruction(PROGRAM_ID, [AccountMeta(BOB, False, False)], b"\x00")
        second = Instruction(PROGRAM_ID, [AccountMeta(BOB, False, True)], b"\x00")
        message = build_message(ALICE, [first, second], BLOCKHASH)
        keys = self.parse_keys(message)
        self.assertEqual(len(keys), 3, "alice, bob, program id - bob only once")
        self.assertEqual(keys[1], BOB, "bob must be promoted to writable")
        self.assertEqual(message[2], 1, "only the program id stays readonly-unsigned")

    def test_fee_payer_stays_first_even_if_listed_readonly(self):
        instruction = Instruction(PROGRAM_ID, [AccountMeta(ALICE, False, False)], b"\x00")
        message = build_message(ALICE, [instruction], BLOCKHASH)
        keys = self.parse_keys(message)
        self.assertEqual(keys[0], ALICE)
        self.assertEqual(message[0], 1, "the fee payer is always a required signer")

    def test_instruction_body_round_trips(self):
        instruction = self.simple()
        message = build_message(ALICE, [instruction], BLOCKHASH)
        keys = self.parse_keys(message)
        offset = 3 + len(compact_u16(len(keys))) + 32 * len(keys)

        self.assertEqual(message[offset : offset + 32], b58decode(BLOCKHASH))
        offset += 32

        self.assertEqual(message[offset], 1, "one instruction")
        offset += 1
        program_index = message[offset]
        self.assertEqual(keys[program_index], PROGRAM_ID)
        offset += 1

        account_count = message[offset]
        offset += 1
        self.assertEqual(account_count, 3)
        indices = list(message[offset : offset + 3])
        offset += 3
        self.assertEqual([keys[i] for i in indices], [ALICE, BOB, MINT],
                         "instruction account order must be preserved")

        data_len = message[offset]
        offset += 1
        self.assertEqual(message[offset : offset + data_len], b"\x01\x02\x03")
        self.assertEqual(offset + data_len, len(message), "no trailing bytes")

    def test_base58_wrapper_matches_raw(self):
        instruction = self.simple()
        self.assertEqual(
            b58decode(build_message_b58(ALICE, [instruction], BLOCKHASH)),
            build_message(ALICE, [instruction], BLOCKHASH),
        )

    def test_empty_transaction_is_rejected(self):
        with self.assertRaises(TxBuildError):
            build_message(ALICE, [], BLOCKHASH)

    def test_bad_blockhash_is_rejected(self):
        with self.assertRaises(TxBuildError):
            build_message(ALICE, [self.simple()], "abc")

    @staticmethod
    def parse_keys(message: bytes):
        count = message[3]  # every fixture here is well under 128 accounts
        offset = 4
        keys = []
        for _ in range(count):
            keys.append(b58encode(message[offset : offset + 32]))
            offset += 32
        return keys


def all_instructions():
    return {
        "initialize_config": txbuild.initialize_config(PROGRAM_ID, ALICE, BOB),
        "update_authority": txbuild.update_authority(PROGRAM_ID, ALICE, BOB),
        "register_mint": txbuild.register_mint(PROGRAM_ID, ALICE, MINT, BOB),
        "write_registration": txbuild.write_registration(PROGRAM_ID, ALICE, BOB, MINT),
        "sync": txbuild.sync(PROGRAM_ID, ALICE, BOB, MINT, TOKEN_PROGRAM_ID),
        "claim": txbuild.claim(PROGRAM_ID, ALICE, MINT, TOKEN_PROGRAM_ID),
        "donate": txbuild.donate(PROGRAM_ID, ALICE, MINT, 1_000),
        "reconcile": txbuild.reconcile(PROGRAM_ID, ALICE, MINT),
    }


class TestInstructionBuilders(unittest.TestCase):
    def test_every_instruction_carries_its_anchor_discriminator(self):
        for name, instruction in all_instructions().items():
            with self.subTest(instruction=name):
                self.assertEqual(
                    instruction.data[:8],
                    discriminator("global", name),
                    "Anchor dispatches on sha256('global:<name>')[:8]",
                )

    def test_builders_cover_every_program_instruction(self):
        lib_rs = (REPO / "programs" / "stackapp" / "src" / "lib.rs").read_text(encoding="utf-8")
        declared = set(re.findall(r"pub fn (\w+)\s*\(\s*ctx:", lib_rs))
        self.assertEqual(set(txbuild.BUILDERS), declared)

    def test_donate_argument_encoding(self):
        instruction = txbuild.donate(PROGRAM_ID, ALICE, MINT, 1_000)
        body = instruction.data[8:]
        self.assertEqual(len(body), 8, "one u64 argument")
        self.assertEqual(int.from_bytes(body, "little"), 1_000)

    def test_pubkey_argument_encoding(self):
        instruction = txbuild.update_authority(PROGRAM_ID, ALICE, BOB)
        body = instruction.data[8:]
        self.assertEqual(len(body), 32)
        self.assertEqual(b58encode(body), BOB)

    def test_registration_pdas_differ_per_wallet(self):
        # accounts: owner, mint, token_config, loyalty_pool, registration, ...
        mine = txbuild.claim(PROGRAM_ID, ALICE, MINT, TOKEN_PROGRAM_ID)
        theirs = txbuild.claim(PROGRAM_ID, BOB, MINT, TOKEN_PROGRAM_ID)
        self.assertNotEqual(mine.accounts[4].pubkey, theirs.accounts[4].pubkey)

    def test_ata_differs_per_token_program(self):
        from stackapp_indexer.ata import TOKEN_2022_PROGRAM_ID

        legacy = txbuild.claim(PROGRAM_ID, ALICE, MINT, TOKEN_PROGRAM_ID)
        token2022 = txbuild.claim(PROGRAM_ID, ALICE, MINT, TOKEN_2022_PROGRAM_ID)
        self.assertNotEqual(
            legacy.accounts[-1].pubkey,
            token2022.accounts[-1].pubkey,
            "the ATA must be derived under the mint's real owning program",
        )

    def test_every_instruction_builds_a_valid_message(self):
        for name, instruction in all_instructions().items():
            with self.subTest(instruction=name):
                payer = instruction.accounts[0].pubkey
                message = build_message(payer, [instruction], BLOCKHASH)
                self.assertGreater(len(message), 40)
                self.assertEqual(message[0], 1, "only the wallet signs")


class TestRustParity(unittest.TestCase):
    """Parse the Rust `#[derive(Accounts)]` structs and compare shapes."""

    @staticmethod
    def rust_accounts(instruction: str):
        source = (INSTRUCTIONS_DIR / f"{instruction}.rs").read_text(encoding="utf-8")
        start = source.index("#[derive(Accounts)]")
        body_start = source.index("{", source.index("pub struct", start))

        depth = 0
        for i in range(body_start, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    body = source[body_start + 1 : i]
                    break
        else:  # pragma: no cover
            raise AssertionError(f"unbalanced braces in {instruction}.rs")

        shape = []
        pending_attr = ""
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line.startswith("#[account("):
                pending_attr = line
                if not line.endswith(")]"):
                    pending_attr = line
                continue
            if pending_attr and not pending_attr.endswith(")]"):
                pending_attr += " " + line
                if not line.endswith(")]"):
                    continue
                continue
            match = re.match(r"pub (\w+)\s*:\s*(.+?),?$", line)
            if not match:
                continue
            name, ty = match.group(1), match.group(2)
            attr = pending_attr
            pending_attr = ""
            is_signer = ty.startswith("Signer<")
            writable = bool(re.search(r"\b(mut|init|init_if_needed)\b", attr))
            shape.append((name, is_signer, writable))
        return shape

    def test_account_shape_matches_rust(self):
        cases = {
            "initialize_config": txbuild.initialize_config(PROGRAM_ID, ALICE, BOB),
            "update_authority": txbuild.update_authority(PROGRAM_ID, ALICE, BOB),
            "register_mint": txbuild.register_mint(PROGRAM_ID, ALICE, MINT, BOB),
            "write_registration": txbuild.write_registration(PROGRAM_ID, ALICE, BOB, MINT),
            "sync": txbuild.sync(PROGRAM_ID, ALICE, BOB, MINT, TOKEN_PROGRAM_ID),
            "claim": txbuild.claim(PROGRAM_ID, ALICE, MINT, TOKEN_PROGRAM_ID),
            "donate": txbuild.donate(PROGRAM_ID, ALICE, MINT, 1),
            "reconcile": txbuild.reconcile(PROGRAM_ID, ALICE, MINT),
        }

        for name, instruction in cases.items():
            with self.subTest(instruction=name):
                rust = self.rust_accounts(name)
                python = instruction.accounts

                self.assertEqual(
                    len(python),
                    len(rust),
                    f"{name}: Python passes {len(python)} accounts, Rust declares "
                    f"{len(rust)} ({[f[0] for f in rust]})",
                )
                for i, (field, is_signer, writable) in enumerate(rust):
                    self.assertEqual(
                        python[i].is_signer,
                        is_signer,
                        f"{name}.{field} (index {i}): signer flag differs",
                    )
                    self.assertEqual(
                        python[i].is_writable,
                        writable,
                        f"{name}.{field} (index {i}): writable flag differs",
                    )


if __name__ == "__main__":
    unittest.main()

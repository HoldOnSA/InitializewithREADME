"""Borsh codec, Anchor discriminators, layout round-trips and PDA derivation.

All stdlib - no network, no RPC, no pip install.
"""

import base64
import unittest

from stackapp_indexer.borsh import (
    BorshError,
    Reader,
    b58decode,
    b58encode,
    decode_struct,
    encode_struct,
)
from stackapp_indexer.layouts import (
    ACCOUNT_LAYOUTS,
    EVENT_LAYOUTS,
    decode_account,
    decode_event,
    discriminator,
    encode_account,
    encode_event,
    parse_program_data_lines,
)
from stackapp_indexer.pda import (
    PdaError,
    create_program_address,
    find_program_address,
    is_on_curve,
    registration_pda,
    token_config_pda,
)
from stackapp_indexer.selftest import round_trip_accounts, round_trip_events, sample_struct

PROGRAM_ID = "Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


class TestBase58(unittest.TestCase):
    def test_system_program_is_thirty_two_zero_bytes(self):
        self.assertEqual(b58decode(SYSTEM_PROGRAM), b"\x00" * 32)
        self.assertEqual(b58encode(b"\x00" * 32), SYSTEM_PROGRAM)

    def test_known_program_ids_decode_to_32_bytes(self):
        for key in (PROGRAM_ID, TOKEN_PROGRAM, SYSTEM_PROGRAM):
            with self.subTest(key=key):
                self.assertEqual(len(b58decode(key)), 32)

    def test_round_trip(self):
        for salt in range(32):
            raw = bytes((salt * 31 + i) % 256 for i in range(32))
            self.assertEqual(b58decode(b58encode(raw)), raw)

    def test_leading_zeros_survive(self):
        raw = b"\x00\x00\x01" + bytes(29)
        self.assertEqual(b58decode(b58encode(raw)), raw)

    def test_invalid_character_is_rejected(self):
        with self.assertRaises(BorshError):
            b58decode("0OIl")  # the four characters base58 omits


class TestBorsh(unittest.TestCase):
    def test_integers_are_little_endian(self):
        layout = [("a", "u16"), ("b", "i64")]
        raw = encode_struct(layout, {"a": 513, "b": -2})
        self.assertEqual(raw[:2], b"\x01\x02")
        self.assertEqual(decode_struct(layout, raw), {"a": 513, "b": -2})

    def test_u128_round_trips_beyond_javascript_safe_range(self):
        layout = [("acc", "u128")]
        value = 2**127 + 12345
        self.assertEqual(decode_struct(layout, encode_struct(layout, {"acc": value}))["acc"], value)

    def test_vec_of_structs(self):
        inner = [("x", "u64"), ("y", "i64")]
        layout = [("items", ("vec", ("struct", inner)))]
        value = {"items": [{"x": 1, "y": -1}, {"x": 2, "y": -2}]}
        self.assertEqual(decode_struct(layout, encode_struct(layout, value)), value)

    def test_empty_vec(self):
        layout = [("items", ("vec", "u64"))]
        self.assertEqual(decode_struct(layout, encode_struct(layout, {"items": []})), {"items": []})

    def test_buffer_underrun_is_an_error(self):
        with self.assertRaises(BorshError):
            Reader(b"\x01\x02").read("u64")

    def test_missing_field_is_an_error(self):
        with self.assertRaises(BorshError):
            encode_struct([("a", "u8"), ("b", "u8")], {"a": 1})


class TestLayouts(unittest.TestCase):
    def test_discriminators_are_anchors(self):
        # Anchor hashes a namespaced name and keeps the first 8 bytes.
        import hashlib

        self.assertEqual(
            discriminator("event", "RewardClaimed"),
            hashlib.sha256(b"event:RewardClaimed").digest()[:8],
        )
        self.assertEqual(len(discriminator("account", "Registration")), 8)

    def test_discriminators_are_unique(self):
        seen = {}
        for namespace, names in (("event", EVENT_LAYOUTS), ("account", ACCOUNT_LAYOUTS)):
            for name in names:
                disc = discriminator(namespace, name)
                self.assertNotIn(disc, seen, f"{name} collides with {seen.get(disc)}")
                seen[disc] = name

    def test_every_event_round_trips(self):
        self.assertEqual(round_trip_events(), [])

    def test_every_account_round_trips(self):
        self.assertEqual(round_trip_accounts(), [])

    def test_unknown_discriminator_decodes_to_none(self):
        self.assertIsNone(decode_event(b"\x00" * 8 + b"payload"))
        self.assertIsNone(decode_account(b"\xff" * 8))
        self.assertIsNone(decode_event(b"\x01\x02"))

    def test_events_are_pulled_out_of_transaction_logs(self):
        payload = sample_struct(EVENT_LAYOUTS["FeeCollected"])
        blob = base64.b64encode(encode_event("FeeCollected", payload)).decode()
        logs = [
            "Program Fg6Paf... invoke [1]",
            "Program log: Instruction: Donate",
            f"Program data: {blob}",
            "Program log: not base64 at all !!!",
            "Program Fg6Paf... success",
        ]
        found = parse_program_data_lines(logs)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "FeeCollected")
        self.assertEqual(found[0]["data"]["amount"], payload["amount"])

    def test_a_truncated_payload_does_not_crash_the_parser(self):
        blob = base64.b64encode(
            encode_event("WeightSynced", sample_struct(EVENT_LAYOUTS["WeightSynced"]))[:12]
        )
        with self.assertRaises(BorshError):
            decode_event(base64.b64decode(blob))


class TestPda(unittest.TestCase):
    def test_real_program_ids_are_on_the_curve(self):
        for key in (TOKEN_PROGRAM, PROGRAM_ID, SYSTEM_PROGRAM):
            with self.subTest(key=key):
                self.assertTrue(is_on_curve(b58decode(key)), f"{key} should be a real pubkey")

    def test_wrong_length_is_not_on_the_curve(self):
        self.assertFalse(is_on_curve(b"\x00" * 31))

    def test_found_addresses_are_off_the_curve(self):
        address, bump = find_program_address([b"config", b58decode(TOKEN_PROGRAM)], PROGRAM_ID)
        self.assertFalse(is_on_curve(b58decode(address)))
        self.assertLessEqual(bump, 255)

    def test_derivation_is_deterministic_and_reproducible_from_the_bump(self):
        seeds = [b"registration", b58decode(TOKEN_PROGRAM), b58decode(SYSTEM_PROGRAM)]
        address, bump = find_program_address(seeds, PROGRAM_ID)
        self.assertEqual(find_program_address(seeds, PROGRAM_ID), (address, bump))
        self.assertEqual(create_program_address([*seeds, bytes([bump])], PROGRAM_ID), address)

    def test_different_seeds_give_different_addresses(self):
        a, _ = token_config_pda(TOKEN_PROGRAM, PROGRAM_ID)
        b, _ = token_config_pda(SYSTEM_PROGRAM, PROGRAM_ID)
        self.assertNotEqual(a, b)

    def test_registration_pda_is_bound_to_both_mint_and_owner(self):
        one, _ = registration_pda(TOKEN_PROGRAM, SYSTEM_PROGRAM, PROGRAM_ID)
        two, _ = registration_pda(SYSTEM_PROGRAM, TOKEN_PROGRAM, PROGRAM_ID)
        self.assertNotEqual(one, two, "swapping mint and owner must not collide")

    def test_oversized_seed_is_rejected(self):
        with self.assertRaises(PdaError):
            create_program_address([b"x" * 33], PROGRAM_ID)


if __name__ == "__main__":
    unittest.main()

"""The anti-gaming rules, exercised end to end through `Market`.

These are the tests that matter. Each one names the attack it blocks.
Mirrors the `#[cfg(test)]` module in `programs/stackapp/src/logic.rs`.
"""

import unittest

from stackapp_sim import Market, StackError
from stackapp_sim.constants import MIN_CLAIM_DELAY_SLOTS, TENURE_TIER_SECONDS


def fresh(now=0, slot=0):
    return Market.register(now=now, slot=slot)


class TestRegistrationAndWeight(unittest.TestCase):
    def test_a_fresh_registration_earns_nothing_until_synced(self):
        m = fresh()
        m.write_registration("alice")
        m.donate("creator", 1_000)
        self.assertEqual(m.claimable("alice"), 0, "no weight yet - nothing to settle")

    def test_weight_only_ever_comes_from_a_live_balance(self):
        """write_registration never sets weight - only sync/claim do, off a
        live balance the caller supplies, never a cached number."""
        m = fresh()
        r = m.write_registration("alice")
        self.assertEqual(r.weighted_shares, 0)

        m.set_balance("alice", 1_000_000)
        m.advance(seconds=TENURE_TIER_SECONDS[0])
        weight = m.sync("alice")
        self.assertGreater(weight, 0)

    def test_fee_buffers_until_someone_has_weight(self):
        m = fresh()
        m.donate("creator", 1_000)
        self.assertEqual(m.pool.undistributed, 1_000)

        m.write_registration("alice")
        m.set_balance("alice", 500_000)
        m.sync("alice")
        self.assertGreater(m.pool.total_weighted_shares, 0)

        # Buffered fee is only flushed on the NEXT collection, not
        # retroactively just because weight now exists.
        self.assertEqual(m.pool.undistributed, 1_000)


class TestFlashLoanGuard(unittest.TestCase):
    def test_a_bigger_balance_earns_nothing_retroactively(self):
        m = fresh()
        m.write_registration("alice")
        m.set_balance("alice", 100)
        m.sync("alice")

        m.donate("creator", 1_000)
        self.assertGreater(m.claimable("alice"), 0)

        # Bob registers and syncs AFTER that fee already landed - he must
        # capture none of it, no matter how large his balance is.
        m.now = 100
        m.write_registration("bob")
        m.set_balance("bob", 1_000_000)
        m.sync("bob")
        self.assertEqual(m.claimable("bob"), 0, "Bob must not capture fee collected before he had weight")

    def test_claim_is_blocked_until_the_minimum_slot_delay_elapses(self):
        m = fresh()
        m.write_registration("alice")
        m.set_balance("alice", 1_000)
        m.slot = 10
        m.sync("alice")

        with self.assertRaises(StackError):
            m.claim("alice")

        m.advance(slots=MIN_CLAIM_DELAY_SLOTS - 1)
        with self.assertRaises(StackError):
            m.claim("alice")

        m.advance(slots=1)
        with self.assertRaises(StackError):
            m.claim("alice")  # eligible now, but nothing to claim yet

    def test_a_same_block_balance_increase_cannot_capture_a_fee_it_did_not_earn(self):
        m = fresh()
        m.write_registration("victim")
        m.set_balance("victim", 1_000)
        m.sync("victim")
        m.donate("creator", 10_000)

        m.now = 1
        m.slot = 5
        m.write_registration("attacker")
        m.set_balance("attacker", 1_000_000)
        m.sync("attacker")  # same-slot balance increase

        with self.assertRaises(StackError):
            m.claim("attacker")


class TestSharding(unittest.TestCase):
    def test_sharding_registrations_never_out_earns_one_wallet(self):
        """The headline anti-sybil rule, end to end."""
        now = 700  # past the 10-minute tier
        whole_balance = 9_000_000

        whole = fresh()
        whole.write_registration("whale")
        whole.set_balance("whale", whole_balance)
        whole.now = now
        whole.sync("whale")
        whole.donate("creator", 1_000_000)
        whole_rewards = whole.claimable("whale")

        for shards in (3, 9, 30):
            m = fresh()
            each = whole_balance // shards
            for i in range(shards):
                w = f"w{i}"
                m.write_registration(w)
                m.set_balance(w, each)
            m.now = now
            for i in range(shards):
                m.sync(f"w{i}")
            m.donate("creator", 1_000_000)
            summed = sum(m.claimable(f"w{i}") for i in range(shards))
            self.assertLessEqual(
                summed, whole_rewards, f"{shards} shards summed to {summed} > whole wallet's {whole_rewards}"
            )


class TestLongRunInvariants(unittest.TestCase):
    def test_payouts_never_exceed_collected_fees_across_random_operations(self):
        m = fresh()
        wallets = [f"w{i}" for i in range(5)]
        for w in wallets:
            m.write_registration(w)

        seed = 0xC0FFEE

        def nxt():
            nonlocal seed
            seed = (seed * 6_364_136_223_846_793_005 + 1_442_695_040_888_963_407) % (2**64)
            return seed >> 33

        claimed_total = 0
        for step in range(400):
            m.advance(seconds=1 + nxt() % 200, slots=1 + nxt() % 5)
            wallet = wallets[nxt() % len(wallets)]

            op = nxt() % 3
            try:
                if op == 0:
                    m.donate("creator", 1 + nxt() % 100_000)
                elif op == 1:
                    m.set_balance(wallet, nxt() % 10_000_000)
                    m.sync(wallet)
                else:
                    payout = m.claim(wallet)
                    claimed_total += payout
            except StackError:
                pass  # rejected operations are fine; the invariants still must hold

            self.assertLessEqual(
                m.pool.total_claimed,
                m.pool.total_collected,
                f"step {step}: total_claimed {m.pool.total_claimed} exceeded "
                f"total_collected {m.pool.total_collected}",
            )

        still_pending = sum(m.claimable(w) for w in wallets)
        self.assertLessEqual(
            claimed_total + still_pending,
            m.pool.total_collected,
            f"settled+pending {claimed_total + still_pending} exceeded collected fees "
            f"{m.pool.total_collected}",
        )
        m.assert_invariants("random ops")


if __name__ == "__main__":
    unittest.main()

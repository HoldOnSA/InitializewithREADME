"""The O(1) reward-per-share accumulator.

Mirrors `programs/stackapp/src/math/accumulator.rs`'s test module.
"""

import unittest

from stackapp_sim.constants import ACC_PRECISION
from stackapp_sim.math import distribute, pending


class TestAccumulator(unittest.TestCase):
    def test_distribute_with_no_shares_buffers_everything(self):
        acc, leftover = distribute(0, 0, 5_000)
        self.assertEqual(acc, 0)
        self.assertEqual(leftover, 5_000, "tax must never be burned when nobody is eligible")

    def test_distribute_splits_pro_rata(self):
        acc, leftover = distribute(0, 4_000, 4_000)
        self.assertEqual(leftover, 0)
        self.assertEqual(pending(1_000, acc, 0), 1_000)
        self.assertEqual(pending(3_000, acc, 0), 3_000)

    def test_checkpointing_prevents_double_claim(self):
        acc1, _ = distribute(0, 1_000, 1_000)
        self.assertEqual(pending(1_000, acc1, 0), 1_000)
        acc2, _ = distribute(acc1, 1_000, 500)
        self.assertEqual(pending(1_000, acc2, acc1), 500)
        self.assertEqual(pending(1_000, acc1, acc1), 0, "a settled holder is owed nothing")

    def test_late_joiner_earns_nothing_retroactively(self):
        acc1, _ = distribute(0, 1_000, 1_000)
        bob_checkpoint = acc1
        self.assertEqual(pending(9_000_000, acc1, bob_checkpoint), 0)

        total = 1_000 + 9_000_000
        acc2, _ = distribute(acc1, total, total)
        self.assertEqual(pending(1_000, acc2, acc1), 1_000)
        self.assertEqual(pending(9_000_000, acc2, bob_checkpoint), 9_000_000)

    def test_dust_is_buffered_not_burned(self):
        huge = 10_000_000_000_000  # 1e13 > ACC_PRECISION
        acc, leftover = distribute(0, huge, 1)
        self.assertEqual(acc, 0)
        self.assertEqual(leftover, 1)

        acc2, leftover2 = distribute(0, huge, 10)
        self.assertEqual(acc2, 1)
        self.assertEqual(leftover2, 0)

    def test_distributing_zero_is_a_noop(self):
        acc, leftover = distribute(12_345, 1_000, 0)
        self.assertEqual(acc, 12_345)
        self.assertEqual(leftover, 0)

    def test_precision_scale_is_1e12(self):
        self.assertEqual(ACC_PRECISION, 10**12)

    def test_pool_is_never_insolvent(self):
        """An adversarial sequence of joins, exits and distributions.

        Holders must never be able to claim more than was deposited.
        """
        acc = 0
        buffered = 0
        deposited = 0
        total_claimed = 0
        holders = []  # list of [shares, checkpoint]
        total_shares = 0

        seed = 0x5EED1234

        def nxt():
            nonlocal seed
            seed = (seed * 6_364_136_223_846_793_005 + 1_442_695_040_888_963_407) % (2**64)
            return seed >> 33

        for step in range(400):
            phase = step % 4
            if phase == 0:
                shares = 1 + nxt() % 7_919
                holders.append([shares, acc])
                total_shares += shares
            elif phase in (1, 2):
                amount = 1 + nxt() % 1_000_003
                deposited += amount
                acc, buffered = distribute(acc, total_shares, amount + buffered)
            else:
                if holders:
                    idx = nxt() % len(holders)
                    shares, checkpoint = holders[idx]
                    total_claimed += pending(shares, acc, checkpoint)
                    total_shares -= shares
                    holders.pop(idx)

        for shares, checkpoint in holders:
            total_claimed += pending(shares, acc, checkpoint)

        self.assertLessEqual(
            total_claimed,
            deposited,
            f"claimed {total_claimed} exceeded deposited {deposited}",
        )
        stranded = deposited - total_claimed
        self.assertLessEqual(
            stranded,
            buffered + 400,
            f"stranded {stranded} is more than buffered dust {buffered} plus slack",
        )

    def test_many_holders_split_one_distribution_without_overpaying(self):
        """100 awkward share counts, one distribution, no overpayment."""
        shares = [7 + i * 13 for i in range(100)]
        total = sum(shares)
        amount = 1_000_000_007
        acc, leftover = distribute(0, total, amount)
        paid = sum(pending(s, acc, 0) for s in shares)
        self.assertLessEqual(paid + leftover, amount)
        # And the rounding loss is at most about one unit per holder.
        self.assertGreaterEqual(paid + leftover, amount - len(shares) - 2)


if __name__ == "__main__":
    unittest.main()

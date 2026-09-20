"""Tenure weighting.

Mirrors the `#[cfg(test)]` module under `programs/stackapp/src/math/tenure.rs`.
"""

import unittest

from stackapp_sim.constants import BPS_DENOMINATOR, TENURE_TIER_MULTIPLIER_BPS, TENURE_TIER_SECONDS
from stackapp_sim.math import ceil_div, tenure_multiplier_bps, weight_for_balance

MIN, MIN10, MIN30 = TENURE_TIER_SECONDS


class TestCeilDiv(unittest.TestCase):
    def test_ceil_div_rounds_up(self):
        self.assertEqual(ceil_div(10, 3), 4)
        self.assertEqual(ceil_div(9, 3), 3)
        self.assertEqual(ceil_div(0, 3), 0)
        self.assertEqual(ceil_div(5, 0), 0)


class TestTenure(unittest.TestCase):
    def test_tier_zero_is_1x(self):
        self.assertEqual(tenure_multiplier_bps(0), BPS_DENOMINATOR)
        self.assertEqual(tenure_multiplier_bps(MIN - 1), BPS_DENOMINATOR)

    def test_multiplier_steps_up_with_age(self):
        self.assertEqual(tenure_multiplier_bps(MIN), TENURE_TIER_MULTIPLIER_BPS[1])
        self.assertEqual(tenure_multiplier_bps(MIN10), TENURE_TIER_MULTIPLIER_BPS[2])
        self.assertEqual(tenure_multiplier_bps(MIN30), TENURE_TIER_MULTIPLIER_BPS[3])
        self.assertEqual(tenure_multiplier_bps(MIN30 + 1_000_000), TENURE_TIER_MULTIPLIER_BPS[3])

    def test_multiplier_schedule_is_bounded_and_non_decreasing(self):
        self.assertEqual(TENURE_TIER_MULTIPLIER_BPS[0], BPS_DENOMINATOR, "tier 0 must be 1.00x")
        self.assertEqual(
            list(TENURE_TIER_MULTIPLIER_BPS),
            sorted(TENURE_TIER_MULTIPLIER_BPS),
            "tiers must not go backwards",
        )
        self.assertLessEqual(max(TENURE_TIER_MULTIPLIER_BPS), 3 * BPS_DENOMINATOR, "cap is 3x")

    def test_negative_held_time_is_clamped_to_the_floor(self):
        self.assertEqual(tenure_multiplier_bps(-100), BPS_DENOMINATOR)

    def test_weight_scales_with_both_balance_and_time(self):
        base = weight_for_balance(1_000_000, 0)
        self.assertEqual(base, 1_000_000)  # 1.00x at tier 0

        held_a_minute = weight_for_balance(1_000_000, MIN)
        self.assertGreater(held_a_minute, base, "holding past the first tier must weigh more")

        double_balance = weight_for_balance(2_000_000, MIN)
        self.assertEqual(double_balance, held_a_minute * 2, "weight is linear in balance")

    def test_a_zero_balance_carries_no_weight(self):
        self.assertEqual(weight_for_balance(0, MIN30), 0)

    def test_sharding_across_wallets_never_out_weighs_one_wallet(self):
        whole_balance = 1_000_003  # deliberately not a round multiple
        held = MIN10
        whole_weight = weight_for_balance(whole_balance, held)

        for shards in (2, 3, 5, 7, 11):
            shard_balance = whole_balance // shards
            shard_weight = weight_for_balance(shard_balance, held)
            summed = shard_weight * shards
            self.assertLessEqual(
                summed,
                whole_weight,
                f"{shards} shards of {shard_balance} summed to {summed} > whole wallet's {whole_weight}",
            )


if __name__ == "__main__":
    unittest.main()

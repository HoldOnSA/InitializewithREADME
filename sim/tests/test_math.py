"""Tax curve, vesting, tenure weighting, reputation scoring and the curve.

Mirrors the `#[cfg(test)]` modules under `programs/stackapp/src/math/`.
"""

import unittest

from stackapp_sim.constants import (
    DAY,
    DEFAULT_VIRTUAL_SOL_RESERVES as V_SOL,
    DEFAULT_VIRTUAL_TOKEN_RESERVES as V_TOK,
    LAMPORTS_PER_SOL as SOL,
    MAX_REPUTATION_TIER,
    MAX_TAX_BPS,
)
from stackapp_sim.math import (
    InvalidTaxCurve,
    buy_cost_lamports,
    lot_weight,
    reputation_score_delta,
    sell_proceeds_lamports,
    spot_price_lamports,
    tax_bps_for_age,
    tax_for_amount,
    tenure_multiplier_bps,
    tier_for_score,
    validate_tax_curve,
    vested_amount,
    vested_bps,
)
from stackapp_sim.state import Lot

CURVE = [(0, 3_000), (3_600, 2_000), (86_400, 1_000), (604_800, 0)]


class TestTaxCurve(unittest.TestCase):
    def test_rate_is_a_descending_step_function(self):
        self.assertEqual(tax_bps_for_age(CURVE, 0), 3_000)
        self.assertEqual(tax_bps_for_age(CURVE, 3_599), 3_000)
        self.assertEqual(tax_bps_for_age(CURVE, 3_600), 2_000)
        self.assertEqual(tax_bps_for_age(CURVE, 86_399), 2_000)
        self.assertEqual(tax_bps_for_age(CURVE, 86_400), 1_000)
        self.assertEqual(tax_bps_for_age(CURVE, 604_800), 0)
        self.assertEqual(tax_bps_for_age(CURVE, 10**18), 0)

    def test_negative_age_pays_the_top_rate(self):
        self.assertEqual(tax_bps_for_age(CURVE, -10_000), 3_000)

    def test_valid_curve_is_accepted(self):
        validate_tax_curve(CURVE)  # must not raise

    def test_curve_must_start_at_zero(self):
        with self.assertRaises(InvalidTaxCurve):
            validate_tax_curve([(60, 1_000)])

    def test_rising_curve_is_rejected(self):
        with self.assertRaises(InvalidTaxCurve):
            validate_tax_curve([(0, 1_000), (3_600, 2_000)])

    def test_unsorted_curve_is_rejected(self):
        with self.assertRaises(InvalidTaxCurve):
            validate_tax_curve([(0, 3_000), (3_600, 2_000), (3_600, 1_000)])

    def test_honeypot_rate_is_rejected(self):
        with self.assertRaises(InvalidTaxCurve):
            validate_tax_curve([(0, 10_000)])

    def test_empty_curve_is_rejected(self):
        with self.assertRaises(InvalidTaxCurve):
            validate_tax_curve([])

    def test_tax_rounds_up_so_dust_sells_cannot_dodge_it(self):
        self.assertEqual(tax_for_amount(1, 3_000), 1)
        self.assertEqual(tax_for_amount(10, 3_000), 3)
        self.assertEqual(tax_for_amount(11, 3_000), 4)  # 3.3 -> 4
        self.assertEqual(tax_for_amount(0, 3_000), 0)
        self.assertEqual(tax_for_amount(1_000, 0), 0)

    def test_splitting_an_exit_never_reduces_tax(self):
        bps = 3_000
        for total in (1, 7, 99, 1_000, 123_457):
            single = tax_for_amount(total, bps)
            for chunk in (1, 2, 3, 7, 13):
                remaining = total
                split_tax = 0
                while remaining > 0:
                    take = min(chunk, remaining)
                    split_tax += tax_for_amount(take, bps)
                    remaining -= take
                self.assertGreaterEqual(
                    split_tax, single, f"total={total} chunk={chunk}"
                )

    def test_tax_never_exceeds_the_amount(self):
        self.assertLessEqual(tax_for_amount(5, MAX_TAX_BPS), 5)


class TestVesting(unittest.TestCase):
    def test_nothing_is_vested_at_purchase(self):
        self.assertEqual(vested_amount(1_000, 100, 100, 10 * DAY), 0)
        self.assertEqual(vested_amount(1_000, 100, 50, 10 * DAY), 0)

    def test_vesting_is_linear(self):
        dur = 10 * DAY
        self.assertEqual(vested_amount(1_000, 0, DAY, dur), 100)
        self.assertEqual(vested_amount(1_000, 0, 5 * DAY, dur), 500)
        self.assertEqual(vested_amount(1_000, 0, 9 * DAY, dur), 900)

    def test_fully_vested_at_and_after_the_deadline(self):
        dur = 10 * DAY
        self.assertEqual(vested_amount(1_000, 0, dur, dur), 1_000)
        self.assertEqual(vested_amount(1_000, 0, 1_000 * DAY, dur), 1_000)

    def test_zero_duration_is_immediately_liquid(self):
        self.assertEqual(vested_amount(1_000, 0, 0, 0), 1_000)

    def test_vesting_is_monotonic_and_never_overshoots(self):
        dur = 7 * DAY
        original = 1_000_003
        prev = 0
        for t in range(0, 9 * DAY, 977):
            v = vested_amount(original, 0, t, dur)
            self.assertGreaterEqual(v, prev, f"vesting went backwards at t={t}")
            self.assertLessEqual(v, original, f"vesting overshot at t={t}")
            prev = v
        self.assertEqual(prev, original)

    def test_bps_tracks_amount(self):
        dur = 4 * DAY
        self.assertEqual(vested_bps(0, 0, dur), 0)
        self.assertEqual(vested_bps(0, DAY, dur), 2_500)
        self.assertEqual(vested_bps(0, 2 * DAY, dur), 5_000)
        self.assertEqual(vested_bps(0, 10 * DAY, dur), 10_000)
        self.assertEqual(vested_bps(0, 123, 0), 10_000)


def held_lot(amount, age):
    """A fully-released lot bought `age` seconds before t=0."""
    return Lot(original=amount, buy_timestamp=-age, cum_released=amount, released=amount)


class TestTenureAndReputation(unittest.TestCase):
    def test_multiplier_steps_up_with_age(self):
        self.assertEqual(tenure_multiplier_bps(0), 10_000)
        self.assertEqual(tenure_multiplier_bps(DAY - 1), 10_000)
        self.assertEqual(tenure_multiplier_bps(DAY), 12_500)
        self.assertEqual(tenure_multiplier_bps(7 * DAY), 15_000)
        self.assertEqual(tenure_multiplier_bps(30 * DAY), 20_000)
        self.assertEqual(tenure_multiplier_bps(90 * DAY), 30_000)
        self.assertEqual(tenure_multiplier_bps(3_650 * DAY), 30_000, "multiplier is capped")
        self.assertEqual(tenure_multiplier_bps(-500), 10_000)

    def test_weight_scales_with_both_capital_and_time(self):
        self.assertEqual(lot_weight(held_lot(1_000, 0), 0), 1_000)
        self.assertEqual(lot_weight(held_lot(1_000, 90 * DAY), 0), 3_000)
        self.assertEqual(
            lot_weight(held_lot(3_000, 0), 0),
            lot_weight(held_lot(1_000, 90 * DAY), 0),
            "3x capital held briefly == 1x capital held to the top tier",
        )

    def test_a_sold_out_lot_carries_no_weight(self):
        lot = held_lot(1_000, 30 * DAY)
        lot.released = 0
        self.assertEqual(lot.remaining(), 0)
        self.assertEqual(lot_weight(lot, 0), 0)

    def test_sharding_does_not_increase_pool_weight(self):
        total = 1_000_003
        age = 45 * DAY
        whole = lot_weight(held_lot(total, age), 0)
        for shards in (2, 3, 7, 100):
            per, remainder = divmod(total, shards)
            summed = sum(
                lot_weight(held_lot(per + (1 if i < remainder else 0), age), 0)
                for i in range(shards)
            )
            self.assertLessEqual(summed, whole, f"{shards} shards out-weighed the whole")

    def test_sharding_does_not_increase_reputation_score(self):
        capital = 37 * SOL + 12_345
        window = 63 * DAY
        whole = reputation_score_delta(capital, window)
        for shards in (2, 5, 13, 50):
            per, remainder = divmod(capital, shards)
            summed = sum(
                reputation_score_delta(per + (1 if i < remainder else 0), window)
                for i in range(shards)
            )
            self.assertLessEqual(summed, whole, f"{shards} shards out-scored the whole")

    def test_sharding_dilutes_tier(self):
        capital = 4 * SOL
        window = 30 * DAY
        whole_score = reputation_score_delta(capital, window)
        shard_score = reputation_score_delta(capital // 4, window)
        self.assertLess(
            tier_for_score(shard_score),
            tier_for_score(whole_score),
            "sharding should strictly reduce per-wallet tier",
        )
        self.assertEqual(shard_score * 4, whole_score, "total score is unchanged")

    def test_score_is_superadditive_never_concave(self):
        window = 11 * DAY
        for a in (1, 999, SOL, 7 * SOL + 3):
            for b in (1, 12_345, 3 * SOL, 91 * SOL - 7):
                joint = reputation_score_delta(a + b, window)
                split = reputation_score_delta(a, window) + reputation_score_delta(b, window)
                self.assertGreaterEqual(joint, split, f"f({a}+{b}) was concave")

    def test_score_units_are_milli_sol_days(self):
        self.assertEqual(reputation_score_delta(SOL, DAY), 1_000)
        self.assertEqual(reputation_score_delta(10 * SOL, DAY), 10_000)
        self.assertEqual(reputation_score_delta(SOL, 0), 0)
        self.assertEqual(reputation_score_delta(0, DAY), 0)

    def test_tiers_follow_thresholds(self):
        self.assertEqual(tier_for_score(0), 0)
        self.assertEqual(tier_for_score(999), 0)
        self.assertEqual(tier_for_score(1_000), 1)
        self.assertEqual(tier_for_score(9_999), 1)
        self.assertEqual(tier_for_score(10_000), 2)
        self.assertEqual(tier_for_score(50_000), 3)
        self.assertEqual(tier_for_score(250_000), 4)
        self.assertEqual(tier_for_score(10**30), MAX_REPUTATION_TIER)


class TestBondingCurve(unittest.TestCase):
    @staticmethod
    def k(sol, tok):
        return sol * tok

    def test_a_buy_always_costs_something(self):
        self.assertGreaterEqual(buy_cost_lamports(V_SOL, V_TOK, 1), 1)

    def test_price_rises_as_supply_is_bought(self):
        chunk = 1_000_000_000_000
        first = buy_cost_lamports(V_SOL, V_TOK, chunk)
        second = buy_cost_lamports(V_SOL + first, V_TOK - chunk, chunk)
        self.assertGreater(second, first)

    def test_buying_out_the_whole_reserve_is_rejected(self):
        self.assertIsNone(buy_cost_lamports(V_SOL, V_TOK, V_TOK))
        self.assertIsNone(buy_cost_lamports(V_SOL, V_TOK, V_TOK + 1))

    def test_k_never_decreases_on_a_buy(self):
        amount = 12_345_678_901
        cost = buy_cost_lamports(V_SOL, V_TOK, amount)
        self.assertGreaterEqual(self.k(V_SOL + cost, V_TOK - amount), self.k(V_SOL, V_TOK))

    def test_k_never_decreases_on_a_sell(self):
        amount = 500_000_000_000
        cost = buy_cost_lamports(V_SOL, V_TOK, amount)
        sol, tok = V_SOL + cost, V_TOK - amount
        back = 123_456_789
        proceeds = sell_proceeds_lamports(sol, tok, back)
        self.assertGreaterEqual(self.k(sol - proceeds, tok + back), self.k(sol, tok))

    def test_round_trip_never_profits_the_trader(self):
        for amount in (1, 1_000, 1_000_000_000, 250_000_000_000):
            cost = buy_cost_lamports(V_SOL, V_TOK, amount)
            proceeds = sell_proceeds_lamports(V_SOL + cost, V_TOK - amount, amount)
            self.assertLessEqual(proceeds, cost, f"round trip of {amount} was free money")

    def test_vault_can_always_pay_out(self):
        sol, tok = V_SOL, V_TOK
        vault = 0
        outstanding = 0
        seed = 0xC0FFEE

        def nxt():
            nonlocal seed
            seed = (seed * 6_364_136_223_846_793_005 + 1_442_695_040_888_963_407) % (2**64)
            return seed >> 33

        for step in range(600):
            if step % 3 != 2 or outstanding == 0:
                amount = 1 + nxt() % 5_000_000_000
                cost = buy_cost_lamports(sol, tok, amount)
                if cost is None:
                    continue
                sol += cost
                tok -= amount
                outstanding += amount
                vault += cost
            else:
                amount = 1 + nxt() % outstanding
                proceeds = sell_proceeds_lamports(sol, tok, amount)
                self.assertLessEqual(proceeds, vault, f"step {step}: vault could not pay out")
                sol -= proceeds
                tok += amount
                outstanding -= amount
                vault -= proceeds

            self.assertGreaterEqual(vault, 0, f"vault went negative at step {step}")
            self.assertEqual(vault, sol - V_SOL, f"vault drifted at step {step}")
            self.assertEqual(outstanding, V_TOK - tok, f"token accounting drifted at step {step}")

    def test_spot_price_is_sane_at_launch(self):
        p = spot_price_lamports(V_SOL, V_TOK, 6)
        self.assertTrue(20 <= p <= 40, f"launch spot price was {p} lamports")
        self.assertEqual(spot_price_lamports(V_SOL, 0, 6), 0)

    def test_spot_price_rises_with_supply_sold(self):
        amount = 500_000_000_000_000
        cost = buy_cost_lamports(V_SOL, V_TOK, amount)
        self.assertGreater(
            spot_price_lamports(V_SOL + cost, V_TOK - amount, 6),
            spot_price_lamports(V_SOL, V_TOK, 6),
        )


if __name__ == "__main__":
    unittest.main()

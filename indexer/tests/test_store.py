"""The queryable view: ingestion, derived numbers and feed filtering.

Driven by the simulator through `MockDriver`, so these tests exercise the same
path the `--mock` server does.
"""

import unittest

from stackapp_indexer.mock import MockDriver
from stackapp_indexer.store import Store, tier_perks
from stackapp_sim.constants import DAY, MIN_CLAIM_DELAY_SLOTS


def bootstrapped():
    store = Store()
    driver = MockDriver(store, seed=7)
    driver.bootstrap()
    return store, driver


class TestIngestion(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()

    def test_launches_are_indexed(self):
        tokens = self.store.list_tokens()
        self.assertEqual(len(tokens), 3)
        for token in tokens:
            self.assertTrue(token["mint"])
            self.assertGreater(token["curve"]["spotPriceLamports"], 0)
            self.assertGreaterEqual(len(token["taxCurve"]), 1)
            self.assertEqual(token["taxCurve"][0]["secondsHeld"], 0)

    def test_positions_are_indexed_per_wallet_and_mint(self):
        mint = self.store.list_tokens()[0]["mint"]
        holders = self.store.positions_for_mint(mint)
        self.assertTrue(holders, "the bootstrap should have created holders")
        for holder in holders:
            self.assertEqual(holder["mint"], mint)
            self.assertGreaterEqual(holder["totalRemaining"], 0)

    def test_feed_is_newest_first(self):
        feed = self.store.feed_view(limit=50)
        self.assertTrue(feed)
        timestamps = [e["data"].get("timestamp", 0) for e in feed]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_stats_reflect_the_store(self):
        stats = self.store.stats()
        self.assertEqual(stats["tokens"], 3)
        self.assertGreater(stats["events"], 0)
        self.assertGreater(stats["positions"], 0)


class TestDerivedNumbers(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()
        self.mint = self.store.list_tokens()[0]["mint"]
        self.holders = self.store.positions_for_mint(self.mint)

    def test_unlock_progress_is_a_percentage(self):
        for holder in self.holders:
            self.assertGreaterEqual(holder["unlockProgressBps"], 0)
            self.assertLessEqual(holder["unlockProgressBps"], 10_000)

    def test_each_lot_reports_its_own_age_tax_and_vesting(self):
        holder = next(h for h in self.holders if h["lots"])
        for lot in holder["lots"]:
            self.assertGreaterEqual(lot["ageSeconds"], 0)
            self.assertGreaterEqual(lot["vestedBps"], 0)
            self.assertLessEqual(lot["vestedBps"], 10_000)
            self.assertGreaterEqual(lot["currentTaxBps"], 0)
            self.assertGreaterEqual(lot["tenureMultiplierBps"], 10_000)
            self.assertEqual(lot["remaining"], lot["locked"] + lot["released"])

    def test_pool_shares_sum_to_at_most_one(self):
        total_ppm = sum(h["poolSharePpm"] for h in self.holders)
        self.assertLessEqual(total_ppm, 1_000_001, "shares must not exceed 100%")

    def test_projected_claimable_never_exceeds_the_pool(self):
        pool = self.store.pool_view(self.mint)
        projected = sum(h["projectedClaimable"] for h in self.holders)
        self.assertLessEqual(projected, pool["outstanding"] + pool["undistributed"])

    def test_claim_eligibility_tracks_the_slot_delay(self):
        holder = self.holders[0]
        self.assertEqual(
            holder["claimEligibleAtSlot"],
            self.store.positions[(self.mint, holder["owner"])]["last_increase_slot"]
            + MIN_CLAIM_DELAY_SLOTS,
        )

    def test_u128_fields_are_serialised_as_strings(self):
        # JSON numbers lose precision past 2^53; the accumulator is u128.
        pool = self.store.pool_view(self.mint)
        self.assertIsInstance(pool["accRewardPerShare"], str)
        passport = self.store.reputation_view(self.holders[0]["owner"])
        self.assertIsInstance(passport["score"], str)
        self.assertIsInstance(passport["totalTenureWeightedVolume"], str)


class TestPassport(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()

    def test_unknown_wallet_gets_an_empty_passport_rather_than_an_error(self):
        passport = self.store.reputation_view("nobody")
        self.assertEqual(passport["score"], "0")
        self.assertEqual(passport["tier"], 0)
        self.assertEqual(passport["activePositions"], 0)
        self.assertEqual(passport["positions"], [])

    def test_passport_aggregates_across_mints(self):
        owner = self.driver.address("diamond")
        passport = self.store.reputation_view(owner)
        self.assertEqual(passport["owner"], owner)
        self.assertGreaterEqual(passport["activePositions"], 1)
        self.assertGreaterEqual(passport["averageHoldSeconds"], 0)
        self.assertGreater(passport["capitalAtRiskLamports"], 0)

    def test_every_tier_has_perks_and_a_name(self):
        for tier in range(5):
            self.assertTrue(tier_perks(tier))
        names = {self.store.reputation_view("nobody")["tierName"]}
        self.assertTrue(all(isinstance(n, str) and n for n in names))

    def test_next_tier_threshold_is_reported_until_the_top(self):
        passport = self.store.reputation_view("nobody")
        self.assertEqual(passport["nextTierAt"], "1000")


class TestFeedFiltering(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()

    def test_filter_by_kind(self):
        buys = self.store.feed_view(limit=200, kinds=["BuyExecuted"])
        self.assertTrue(buys)
        self.assertTrue(all(e["name"] == "BuyExecuted" for e in buys))

    def test_filter_by_mint(self):
        mint = self.store.list_tokens()[1]["mint"]
        events = self.store.feed_view(limit=200, mint=mint)
        self.assertTrue(events)
        self.assertTrue(all(e["data"].get("mint") == mint for e in events))

    def test_filter_by_owner(self):
        owner = self.driver.address("diamond")
        events = self.store.feed_view(limit=200, owner=owner)
        for event in events:
            self.assertIn(
                owner,
                (
                    event["data"].get("owner"),
                    event["data"].get("buyer"),
                    event["data"].get("payer"),
                    event["data"].get("destination"),
                ),
            )

    def test_limit_is_respected(self):
        self.assertLessEqual(len(self.store.feed_view(limit=5)), 5)


class TestLiveStepping(unittest.TestCase):
    def test_stepping_the_mock_keeps_the_invariants(self):
        store, driver = bootstrapped()
        before = len(store.feed)
        for _ in range(60):
            driver.step()  # asserts market invariants internally
        self.assertGreater(len(store.feed), before, "stepping should produce events")

        for mint in list(store.configs):
            pool = store.pool_view(mint)
            self.assertGreaterEqual(pool["outstanding"], 0)
            projected = sum(
                h["projectedClaimable"] for h in store.positions_for_mint(mint)
            )
            self.assertLessEqual(projected, pool["outstanding"] + pool["undistributed"])

    def test_feed_is_capped(self):
        from stackapp_indexer.store import MAX_FEED_EVENTS

        store, driver = bootstrapped()
        for _ in range(200):
            driver.step()
        self.assertLessEqual(len(store.feed), MAX_FEED_EVENTS)


if __name__ == "__main__":
    unittest.main()

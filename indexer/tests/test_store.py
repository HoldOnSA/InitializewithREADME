"""The queryable view: ingestion, derived numbers and feed filtering.

Driven by the simulator through `MockDriver`, so these tests exercise the same
path the `--mock` server does.
"""

import unittest

from stackapp_indexer.mock import MockDriver
from stackapp_indexer.store import MAX_FEED_EVENTS, Store


def bootstrapped():
    store = Store()
    driver = MockDriver(store, seed=7)
    driver.bootstrap()
    return store, driver


class TestIngestion(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()

    def test_tokens_are_indexed(self):
        tokens = self.store.list_tokens()
        self.assertEqual(len(tokens), 3)
        for token in tokens:
            self.assertTrue(token["mint"])
            self.assertGreaterEqual(token["registrationCount"], 0)
            self.assertEqual(token["registrationMarkerLamports"], 2_500_000)

    def test_registrations_are_indexed_per_wallet_and_mint(self):
        mint = self.store.list_tokens()[0]["mint"]
        rows = self.store.registrations_for_mint(mint)
        self.assertTrue(rows, "the bootstrap should have created registrations")
        for row in rows:
            self.assertEqual(row["mint"], mint)
            self.assertGreaterEqual(row["weightedShares"], 0)

    def test_feed_is_newest_first(self):
        feed = self.store.feed_view(limit=50)
        self.assertTrue(feed)
        timestamps = [e["data"].get("timestamp", 0) for e in feed]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_stats_reflect_the_store(self):
        stats = self.store.stats()
        self.assertEqual(stats["tokens"], 3)
        self.assertGreater(stats["events"], 0)
        self.assertGreater(stats["registrations"], 0)

    def test_pending_registrations_are_queued_not_written(self):
        mint = self.store.list_tokens()[0]["mint"]
        pending = self.store.pending_registrations_for_mint(mint)
        self.assertTrue(pending, "bootstrap seeds a couple of pending candidates")
        for p in pending:
            self.assertEqual(p["amount"], 2_500_000)
            self.assertNotIn((mint, p["owner"]), self.store.registrations)


class TestDerivedNumbers(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()
        self.mint = self.store.list_tokens()[0]["mint"]
        self.registrations = self.store.registrations_for_mint(self.mint)

    def test_tenure_multiplier_is_at_least_the_floor(self):
        for r in self.registrations:
            self.assertGreaterEqual(r["tenureMultiplierBps"], 10_000)

    def test_pool_shares_sum_to_at_most_one(self):
        total_ppm = sum(r["poolSharePpm"] for r in self.registrations)
        self.assertLessEqual(total_ppm, 1_000_001, "shares must not exceed 100%")

    def test_projected_claimable_never_exceeds_the_pool(self):
        pool = self.store.pool_view(self.mint)
        projected = sum(r["projectedClaimable"] for r in self.registrations)
        self.assertLessEqual(projected, pool["outstanding"] + pool["undistributed"])

    def test_claim_eligibility_tracks_the_slot_delay(self):
        from stackapp_sim.constants import MIN_CLAIM_DELAY_SLOTS

        r = self.registrations[0]
        raw = self.store.registrations[(self.mint, r["owner"])]
        self.assertEqual(r["claimEligibleAtSlot"], raw["last_sync_slot"] + MIN_CLAIM_DELAY_SLOTS)

    def test_u128_fields_are_serialised_as_strings(self):
        # JSON numbers lose precision past 2^53; the accumulator is u128.
        pool = self.store.pool_view(self.mint)
        self.assertIsInstance(pool["accRewardPerShare"], str)


class TestFeedFiltering(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()

    def test_filter_by_kind(self):
        claims = self.store.feed_view(limit=200, kinds=["FeeCollected"])
        self.assertTrue(claims)
        self.assertTrue(all(e["name"] == "FeeCollected" for e in claims))

    def test_filter_by_mint(self):
        mint = self.store.list_tokens()[1]["mint"]
        events = self.store.feed_view(limit=200, mint=mint)
        self.assertTrue(events)
        self.assertTrue(all(e["data"].get("mint") == mint for e in events))

    def test_filter_by_owner(self):
        owner = self.driver.address("diamond")
        events = self.store.feed_view(limit=200, owner=owner)
        for event in events:
            self.assertEqual(event["data"].get("owner"), owner)

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
            projected = sum(r["projectedClaimable"] for r in store.registrations_for_mint(mint))
            self.assertLessEqual(projected, pool["outstanding"] + pool["undistributed"])

    def test_feed_is_capped(self):
        store, driver = bootstrapped()
        for _ in range(200):
            driver.step()
        self.assertLessEqual(len(store.feed), MAX_FEED_EVENTS)


class TestVaultReconciliation(unittest.TestCase):
    def setUp(self):
        self.store, self.driver = bootstrapped()
        self.market = self.driver.markets[0]
        self.driver.sync(self.market)

    def test_deposit_vault_fields_decode_into_the_token_view(self):
        mint = self.market.config.mint
        self.market.write_registration(self.driver.address("fresh-wallet"))
        self.driver.sync(self.market)

        view = self.store.token_view(mint)
        self.assertEqual(view["totalMarkerDeposits"], self.market.vault.total_marker_deposits)
        self.assertEqual(view["totalRentSpent"], self.market.vault.total_rent_spent)
        self.assertGreater(view["totalMarkerDeposits"], 0)

    def test_a_raw_sol_transfer_outside_donate_shows_up_as_a_pending_surplus(self):
        """The case this whole feature exists for: money that never went
        through `donateIx` at all."""
        mint = self.market.config.mint
        before = self.store.token_view(mint)["pendingReconcileSurplus"]
        self.assertEqual(before, 0)

        self.market.vault_lamports += 60_000
        self.driver.sync(self.market)

        after = self.store.token_view(mint)["pendingReconcileSurplus"]
        self.assertEqual(after, 60_000)

        # Reconciling sweeps it into the pool, so the view agrees afterward.
        collected_before = self.store.token_view(mint)["pool"]["totalCollected"]
        self.market.reconcile()
        self.driver.sync(self.market)

        view = self.store.token_view(mint)
        self.assertEqual(view["pendingReconcileSurplus"], 0)
        self.assertEqual(view["pool"]["totalCollected"], collected_before + 60_000)


if __name__ == "__main__":
    unittest.main()

"""The anti-gaming rules, exercised end to end through `Market`.

These are the tests that matter. Each one names the attack it blocks.
Mirrors the `#[cfg(test)]` module in `programs/stackapp/src/logic.rs`.
"""

import unittest

from stackapp_sim import TAX_CURVE_PRESETS, Market, StackError
from stackapp_sim.constants import DAY, LAMPORTS_PER_SOL as SOL, MAX_LOTS, MIN_CLAIM_DELAY_SLOTS
from stackapp_sim.math import tax_bps_for_age, tax_for_amount

CURVE = TAX_CURVE_PRESETS["diamond"]  # 30% -> 20% -> 10% -> 0% over a week


def fresh(vest=0, now=0, slot=0):
    return Market.launch(CURVE, vest_duration_seconds=vest, now=now, slot=slot)


class TestVesting(unittest.TestCase):
    def test_vesting_releases_linearly_into_spendable(self):
        m = fresh(vest=10 * DAY)
        m.buy("alice", 1_000)
        self.assertEqual(m.position("alice").spendable, 0, "a fresh buy is fully locked")

        m.advance(2 * DAY)
        self.assertEqual(m.claim_vested("alice"), 200)
        m.advance(3 * DAY)
        self.assertEqual(m.claim_vested("alice"), 300)
        self.assertEqual(m.position("alice").spendable, 500)

        m.advance(95 * DAY)
        self.assertEqual(m.claim_vested("alice"), 500)
        self.assertEqual(m.position("alice").spendable, 1_000)
        self.assertEqual(m.position("alice").vested_claimed, 1_000)

        with self.assertRaises(StackError):
            m.claim_vested("alice")  # nothing left to release
        m.assert_invariants("vesting")

    def test_cannot_sell_more_than_is_vested(self):
        m = fresh(vest=10 * DAY)
        m.buy("alice", 1_000)
        m.advance(2 * DAY)
        m.claim_vested("alice")
        self.assertEqual(m.position("alice").spendable, 200)
        with self.assertRaises(StackError):
            m.sell("alice", 201)


class TestFifoLots(unittest.TestCase):
    def test_fifo_charges_more_than_average_cost_after_a_top_up(self):
        """The reason lots exist.

        An average-timestamp model lets fresh capital hide behind an old
        position's tenure. FIFO does not.
        """
        m = fresh()
        m.buy("alice", 1_000)          # old money
        m.advance(30 * DAY)
        m.buy("alice", 1_000)          # fresh money, same wallet
        m.claim_vested("alice")

        out = m.sell("alice", 2_000)

        # What an average-age model would have charged: mean age 15 days, which
        # is past the 7-day 0% tier on this curve.
        average_model_tax = tax_for_amount(2_000, tax_bps_for_age(CURVE, 15 * DAY))
        self.assertEqual(average_model_tax, 0)
        self.assertEqual(out.tax, 300, "FIFO charges 30% on the fresh 1_000 only")
        self.assertGreater(out.tax, average_model_tax)

    def test_fifo_consumes_the_oldest_lot_first(self):
        m = fresh()
        m.buy("alice", 1_000)
        m.advance(30 * DAY)
        m.buy("alice", 1_000)
        m.claim_vested("alice")

        out = m.sell("alice", 1_000)
        self.assertEqual(out.tax, 0, "the 30-day-old lot goes first, at 0%")
        lots = m.position("alice").lots
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].buy_timestamp, 30 * DAY, "the fresh lot must be what remains")

        out2 = m.sell("alice", 1_000)
        self.assertEqual(out2.tax, 300, "what is left is fresh, so it pays the top rate")
        self.assertEqual(m.position("alice").lots, [])
        m.assert_invariants("fifo")

    def test_same_second_buys_merge_instead_of_growing_lots(self):
        m = fresh()
        for _ in range(50):
            m.buy("alice", 10)
        self.assertEqual(len(m.position("alice").lots), 1)
        self.assertEqual(m.position("alice").lots[0].original, 500)

    def test_lot_capacity_is_enforced(self):
        m = fresh()
        for _ in range(MAX_LOTS):
            m.buy("alice", 10)
            m.advance(1)
        self.assertEqual(len(m.position("alice").lots), MAX_LOTS)
        with self.assertRaises(StackError):
            m.buy("alice", 10)

    def test_compaction_takes_the_newer_timestamp(self):
        m = fresh()
        m.buy("alice", 1_000)
        m.advance(30 * DAY)
        m.buy("alice", 1_000)
        m.compact_lots("alice")

        lots = m.position("alice").lots
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].original, 2_000)
        self.assertEqual(
            lots[0].buy_timestamp,
            30 * DAY,
            "compaction must not launder fresh tokens into an old lot's rate",
        )


class TestTransfersAreSells(unittest.TestCase):
    def test_transfer_is_taxed_identically_to_a_sell(self):
        """The headline rule: moving tokens to another wallet is not a loophole."""
        def build():
            m = fresh()
            m.buy("alice", 5_000)
            m.advance(2 * DAY)
            m.claim_vested("alice")
            return m

        sold = build().sell("alice", 4_000)

        m2 = build()
        moved = m2.transfer("alice", "bob", 4_000)

        self.assertEqual(moved.tax, sold.tax)
        self.assertEqual(moved.net, sold.net)
        self.assertGreater(moved.tax, 0, "a 2-day-old exit is not free")
        self.assertEqual(moved.top_tax_bps, sold.top_tax_bps)

    def test_transferred_tokens_restart_the_tenure_clock(self):
        m = fresh()
        m.buy("alice", 10_000)
        m.advance(30 * DAY)
        m.claim_vested("alice")

        moved = m.transfer("alice", "bob", 10_000)
        self.assertEqual(moved.tax, 0, "alice's own tokens were mature")

        # Bob dumping immediately pays the top rate, not alice's 0%.
        dumped = m.sell("bob", moved.net)
        self.assertEqual(dumped.tax, tax_for_amount(moved.net, 3_000))
        self.assertGreater(dumped.tax, 0)
        m.assert_invariants("transfer tenure")

    def test_laundering_tenure_through_a_fresh_wallet_is_strictly_worse(self):
        """Buy old, transfer to a clean wallet, sell there: never cheaper."""
        direct = fresh()
        direct.buy("alice", 10_000)
        direct.advance(2 * DAY)
        direct.claim_vested("alice")
        direct_out = direct.sell("alice", 10_000)

        laundered = fresh()
        laundered.buy("alice", 10_000)
        laundered.advance(2 * DAY)
        laundered.claim_vested("alice")
        hop = laundered.transfer("alice", "burner", 10_000)
        hop_out = laundered.sell("burner", hop.net)

        total_tax = hop.tax + hop_out.tax
        self.assertGreater(
            total_tax,
            direct_out.tax,
            "hopping wallets should cost more, not less",
        )

    def test_self_transfer_is_rejected(self):
        m = fresh()
        m.buy("alice", 1_000)
        m.claim_vested("alice")
        with self.assertRaises(StackError):
            m.transfer("alice", "alice", 100)

    def test_pool_donation_is_exempt_but_forfeits_the_tokens(self):
        m = fresh()
        m.buy("alice", 1_000)
        m.advance(60)
        m.claim_vested("alice")

        out = m.donate_to_pool("alice", 1_000)
        self.assertEqual(out.tax, 0, "donations to the pool are exempt")
        self.assertEqual(out.net, 1_000)
        self.assertEqual(m.position("alice").total_remaining(), 0, "the donor keeps nothing")
        self.assertEqual(m.pool.total_collected, 1_000)

    def test_transfer_to_the_pool_address_is_not_a_transfer_path(self):
        m = fresh()
        m.buy("alice", 1_000)
        m.claim_vested("alice")
        with self.assertRaises(StackError):
            m.transfer("alice", m.config.pool_account, 100)

    def test_splitting_a_sell_into_dust_does_not_reduce_tax(self):
        one_shot = fresh()
        one_shot.buy("alice", 1_000)
        one_shot.advance(60)
        one_shot.claim_vested("alice")
        single = one_shot.sell("alice", 1_000).tax

        dribble = fresh()
        dribble.buy("alice", 1_000)
        dribble.advance(60)
        dribble.claim_vested("alice")
        dust_tax = sum(dribble.sell("alice", 1).tax for _ in range(1_000))

        self.assertGreaterEqual(
            dust_tax, single, f"1_000 dust sells paid {dust_tax} vs {single} in one sell"
        )


class TestRewardAccumulator(unittest.TestCase):
    def test_a_buy_earns_nothing_retroactively(self):
        m = fresh()
        m.buy("alice", 1_000)
        m.buy("seller", 1_000)
        m.advance(10)
        m.claim_vested("alice")
        m.claim_vested("seller")
        m.sell("seller", 1_000)
        self.assertGreater(m.pool.total_collected, 0)

        # Bob arrives afterwards with a far larger position.
        m.advance(1)
        m.buy("bob", 1_000_000)
        self.assertEqual(m.claimable("bob"), 0, "Bob must not capture tax collected before he joined")
        self.assertGreater(m.claimable("alice"), 0, "Alice was there and is owed her share")

    def test_claim_is_blocked_until_the_minimum_slot_delay_elapses(self):
        m = fresh()
        m.buy("alice", 10_000_000)
        m.buy("seller", 10_000_000)
        m.advance(10)
        m.claim_vested("alice")
        m.claim_vested("seller")
        m.sell("seller", 10_000_000)

        # Alice's last balance increase was her buy; the pool owes her, but the
        # delay has not elapsed for a position that just changed.
        m.buy("alice", 1)  # resets the delay in the current slot
        self.assertGreater(m.claimable("alice"), 0)
        with self.assertRaises(StackError):
            m.claim_pool_share("alice")

        m.advance(slots=MIN_CLAIM_DELAY_SLOTS - 1)
        with self.assertRaises(StackError):
            m.claim_pool_share("alice")

        m.advance(slots=1)
        self.assertGreater(m.claim_pool_share("alice"), 0)

    def test_same_block_flash_position_cannot_claim(self):
        """Buy huge, have a victim's tax land in the same block, claim.

        The slot gate makes the last step impossible, so the attacker has to
        carry real price risk for several slots to realise anything.
        """
        m = fresh()
        m.buy("honest", 10_000_000)
        m.advance(10)
        m.claim_vested("honest")

        attack_slot = m.slot
        m.buy("attacker", 100_000_000)          # same slot
        m.buy("victim", 10_000)
        m.claim_vested("victim")
        m.sell("victim", 10_000)                # tax lands in the same slot

        self.assertEqual(m.slot, attack_slot, "the whole attack is one block")
        with self.assertRaises(StackError):
            m.claim_pool_share("attacker")

        # The honest holder, whose position predates the block, is unaffected.
        self.assertGreater(m.claimable("honest"), 0)

    def test_tax_with_no_eligible_holders_is_buffered_then_paid_out(self):
        m = fresh()
        m.buy("lone", 1_000)
        m.advance(10)
        m.claim_vested("lone")
        out = m.sell("lone", 1_000)  # full exit, so weight hits zero

        self.assertGreater(out.tax, 0)
        self.assertEqual(m.pool.total_weighted_shares, 0)
        self.assertEqual(m.pool.acc_reward_per_share, 0)
        self.assertEqual(m.pool.undistributed, out.tax, "tax must be buffered, not burned")

        m.advance(10)
        m.buy("newcomer", 2_000)
        m.advance(1)
        m.claim_vested("newcomer")

        self.assertEqual(m.pool.undistributed, 0, "the buffer flushes to the first eligible holder")
        self.assertGreater(m.claimable("newcomer"), 0)
        self.assertLessEqual(m.claimable("newcomer"), out.tax)

    def test_sharding_across_wallets_does_not_out_earn_one_wallet(self):
        """The headline anti-sybil rule, end to end."""
        whole = fresh()
        whole.buy("whale", 4_000)
        whole.buy("seller", 100_000)
        whole.advance(10)
        whole.claim_vested("whale")
        whole.claim_vested("seller")
        whole.sell("seller", 100_000)
        whole_rewards = whole.claimable("whale")

        shards = fresh()
        for i in range(4):
            shards.buy(f"shard{i}", 1_000)
        shards.buy("seller", 100_000)
        shards.advance(10)
        for i in range(4):
            shards.claim_vested(f"shard{i}")
        shards.claim_vested("seller")
        shards.sell("seller", 100_000)
        shard_rewards = sum(shards.claimable(f"shard{i}") for i in range(4))

        self.assertGreater(whole_rewards, 0)
        self.assertLessEqual(
            shard_rewards,
            whole_rewards,
            f"4 shards earned {shard_rewards} vs {whole_rewards} held whole",
        )

    def test_reward_payouts_are_liquid_but_freshly_stamped(self):
        m = fresh()
        m.buy("alice", 10_000_000)
        m.advance(30 * DAY)
        m.claim_vested("alice")          # alice's lot is now mature, 0% tier

        m.buy("seller", 10_000_000)      # a fresh buyer who dumps immediately
        m.claim_vested("seller")
        m.sell("seller", 10_000_000)
        self.assertGreater(m.pool.total_collected, 0)

        before = m.position("alice").spendable
        payout = m.claim_pool_share("alice")
        self.assertGreater(payout, 0)
        self.assertEqual(
            m.position("alice").spendable, before + payout, "rewards are immediately spendable"
        )

        # Selling everything: the mature lot goes out at 0%, but the reward lot
        # is stamped *now*, so dumping it costs the top of the curve.
        out = m.sell("alice", before + payout)
        self.assertEqual(out.tax, tax_for_amount(payout, 3_000))
        self.assertEqual(out.top_tax_bps, 3_000)
        m.assert_invariants("rewards")

    def test_pool_never_pays_out_more_than_it_collected(self):
        m = fresh()
        m.buy("a", 5_000_000)
        m.buy("b", 3_000_000)
        m.buy("c", 9_000_000)
        m.advance(2 * DAY)
        for w in ("a", "b", "c"):
            m.claim_vested(w)
        m.sell("c", 9_000_000)
        m.advance(slots=MIN_CLAIM_DELAY_SLOTS)

        paid = m.claim_pool_share("a") + m.claim_pool_share("b")
        self.assertLessEqual(paid, m.pool.total_collected)
        m.assert_invariants("solvency")


class TestReputation(unittest.TestCase):
    def test_holding_to_maturity_credits_reputation_and_tiers_up(self):
        m = fresh(vest=7 * DAY)
        m.buy("alice", 100_000_000_000_000)  # ~3 SOL of capital at risk
        m.advance(8 * DAY)
        m.claim_vested("alice")

        delta = m.update_reputation("alice")
        rep = m.reputation("alice")
        self.assertGreater(delta, 0)
        self.assertEqual(rep.score, delta)
        self.assertGreaterEqual(rep.tier, 1)
        self.assertEqual(rep.tokens_held_to_maturity, 1)
        self.assertTrue(any(e.kind == "TierUp" for e in m.events))

    def test_reputation_cannot_be_farmed_by_calling_repeatedly(self):
        m = fresh(vest=7 * DAY)
        m.buy("alice", 100_000_000_000_000)
        m.advance(8 * DAY)
        m.claim_vested("alice")
        first = m.update_reputation("alice")

        with self.assertRaises(StackError):
            m.update_reputation("alice")  # window has not reopened

        m.advance(7 * DAY)
        second = m.update_reputation("alice")
        self.assertGreater(second, 0)
        self.assertLess(second, first * 2, "the second window is shorter than the first")

    def test_a_full_exit_forfeits_maturity(self):
        m = fresh(vest=7 * DAY)
        m.buy("alice", 100_000_000_000_000)
        m.advance(8 * DAY)
        m.claim_vested("alice")
        m.sell("alice", 80_000_000_000_000)  # 80% out

        with self.assertRaises(StackError):
            m.update_reputation("alice")

    def test_sharding_reputation_across_wallets_loses_tier(self):
        """Same capital, same time, four wallets instead of one."""
        whole = fresh(vest=7 * DAY)
        whole.buy("whale", 100_000_000_000_000)
        whole.advance(8 * DAY)
        whole.claim_vested("whale")
        whole.update_reputation("whale")

        shards = fresh(vest=7 * DAY)
        for i in range(4):
            shards.buy(f"shard{i}", 25_000_000_000_000)
        shards.advance(8 * DAY)
        for i in range(4):
            shards.claim_vested(f"shard{i}")
            shards.update_reputation(f"shard{i}")

        whole_tier = whole.reputation("whale").tier
        shard_tiers = [shards.reputation(f"shard{i}").tier for i in range(4)]
        total_shard_score = sum(shards.reputation(f"shard{i}").score for i in range(4))

        self.assertTrue(
            all(t < whole_tier for t in shard_tiers),
            f"every shard should rank below the whale ({shard_tiers} vs {whole_tier})",
        )
        self.assertLessEqual(
            total_shard_score,
            whole.reputation("whale").score,
            "sharding must not increase total score either",
        )

    def test_reputation_is_not_transferable(self):
        """There is no code path that moves a score between wallets."""
        m = fresh(vest=7 * DAY)
        m.buy("alice", 100_000_000_000_000)
        m.advance(8 * DAY)
        m.claim_vested("alice")
        m.update_reputation("alice")

        m.transfer("alice", "bob", m.position("alice").spendable)
        self.assertEqual(m.reputation("bob").score, 0, "score does not travel with tokens")
        self.assertGreater(m.reputation("alice").score, 0)


class TestLongRunInvariants(unittest.TestCase):
    def test_invariants_hold_across_a_random_op_sequence(self):
        """Many wallets, many steps, every invariant checked at every step."""
        m = fresh(vest=3 * DAY)
        wallets = [f"w{i}" for i in range(6)]
        seed = 0xD15EA5E

        def nxt():
            nonlocal seed
            seed = (seed * 6_364_136_223_846_793_005 + 1_442_695_040_888_963_407) % (2**64)
            return seed >> 33

        for step in range(400):
            m.advance(seconds=1 + nxt() % 20_000)
            wallet = wallets[nxt() % len(wallets)]
            position = m.position(wallet)
            op = nxt() % 5

            try:
                if op == 0:
                    if len(position.lots) < MAX_LOTS:
                        m.buy(wallet, 1 + nxt() % 100_000_000)
                elif op == 1:
                    m.claim_vested(wallet)
                elif op == 2:
                    if position.spendable > 0:
                        m.sell(wallet, 1 + nxt() % position.spendable)
                elif op == 3:
                    if position.spendable > 0:
                        other = wallets[nxt() % len(wallets)]
                        if other != wallet:
                            m.transfer(wallet, other, 1 + nxt() % position.spendable)
                else:
                    m.claim_pool_share(wallet)
            except StackError:
                pass  # rejected operations are fine; the invariants still must hold

            m.assert_invariants(f"step {step}")

        # Nothing was created or destroyed over 400 random operations.
        pool_held = m.pool.total_collected - m.pool.total_claimed
        self.assertEqual(m.config.tokens_sold, m.total_outstanding() + pool_held)
        self.assertGreater(m.pool.total_collected, 0, "the run should have exercised the tax path")


if __name__ == "__main__":
    unittest.main()

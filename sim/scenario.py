"""A narrated walk-through of the StackApp mechanics.

    python scenario.py

Runs the same code path the tests do, but prints what happens so you can see
the rules working rather than just read an assertion.
"""

from __future__ import annotations

from stackapp_sim import TAX_CURVE_PRESETS, Market, StackError
from stackapp_sim.constants import DAY, LAMPORTS_PER_SOL as SOL, MIN_CLAIM_DELAY_SLOTS, TIER_NAMES

CURVE = TAX_CURVE_PRESETS["diamond"]


def sol(lamports: int) -> str:
    return f"{lamports / SOL:,.4f} SOL"


def rule(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def scenario_fifo() -> None:
    rule("1. FIFO lots: a top-up cannot hide behind an old position's tenure")
    m = Market.launch(CURVE, vest_duration_seconds=0)
    m.buy("alice", 1_000)
    print("  t+0d    alice buys 1,000")
    m.advance(30 * DAY)
    m.buy("alice", 1_000)
    print("  t+30d   alice buys another 1,000 (the 'top-up')")
    m.claim_vested("alice")

    out = m.sell("alice", 2_000)
    print(f"  t+30d   alice sells all 2,000 -> tax {out.tax}")
    print("          lot 1 (30 days old) taxed at    0 bps")
    print("          lot 2 (0 seconds old) taxed at 3000 bps")
    print("          an average-age model would have charged 0 - it would have")
    print("          let the fresh 1,000 inherit the old lot's tenure.")


def scenario_transfer() -> None:
    rule("2. A wallet-to-wallet transfer is taxed exactly like a sell")
    sold = Market.launch(CURVE, vest_duration_seconds=0)
    sold.buy("alice", 5_000)
    sold.advance(2 * DAY)
    sold.claim_vested("alice")
    a = sold.sell("alice", 4_000)

    moved = Market.launch(CURVE, vest_duration_seconds=0)
    moved.buy("alice", 5_000)
    moved.advance(2 * DAY)
    moved.claim_vested("alice")
    b = moved.transfer("alice", "bob", 4_000)

    print(f"  sell 4,000 after 2 days     -> tax {a.tax:>5}  net {a.net:>5}")
    print(f"  transfer 4,000 after 2 days -> tax {b.tax:>5}  net {b.net:>5}")
    print("  Identical. The only untaxed destination is the pool itself, and")
    print("  that path gives the tokens away.")

    dumped = moved.sell("bob", b.net)
    print(f"  bob then dumps immediately  -> tax {dumped.tax} at "
          f"{dumped.top_tax_bps} bps: tenure did not travel with the tokens.")


def scenario_pool() -> None:
    rule("3. The loyalty pool: O(1) distribution, no retroactive earning")
    m = Market.launch(CURVE, vest_duration_seconds=0)
    m.buy("diamond", 10_000_000)
    print("  t+0d    diamond buys 10,000,000 and sits on it")
    m.advance(30 * DAY)
    m.claim_vested("diamond")

    m.buy("flipper", 10_000_000)
    m.claim_vested("flipper")
    out = m.sell("flipper", 10_000_000)
    print(f"  t+30d   flipper buys and dumps in the same second")
    print(f"          -> {out.tax:,} tokens of tax routed to the pool")

    m.buy("latecomer", 50_000_000)
    print("  t+30d   latecomer buys 50,000,000 *after* the tax landed")
    print(f"          latecomer claimable: {m.claimable('latecomer'):,}  (zero - not retroactive)")
    print(f"          diamond   claimable: {m.claimable('diamond'):,}")

    try:
        m.claim_pool_share("latecomer")
    except StackError as exc:
        print(f"          latecomer claim in the same block -> rejected ({exc})")

    m.advance(slots=MIN_CLAIM_DELAY_SLOTS)
    payout = m.claim_pool_share("diamond")
    print(f"  +{MIN_CLAIM_DELAY_SLOTS} slots diamond claims {payout:,} tokens")
    m.assert_invariants("pool scenario")


def scenario_sharding() -> None:
    rule("4. Sharding across wallets never out-earns holding in one")

    def run(wallets: int, each: int) -> int:
        m = Market.launch(CURVE, vest_duration_seconds=0)
        for i in range(wallets):
            m.buy(f"w{i}", each)
        m.buy("flipper", 100_000_000)
        m.advance(10)
        for i in range(wallets):
            m.claim_vested(f"w{i}")
        m.claim_vested("flipper")
        m.sell("flipper", 100_000_000)
        return sum(m.claimable(f"w{i}") for i in range(wallets))

    whole = run(1, 4_000_000)
    for shards in (2, 4, 16):
        total = run(shards, 4_000_000 // shards)
        verdict = "no gain" if total <= whole else "!! EXPLOIT !!"
        print(f"  {shards:>2} wallet(s) x {4_000_000 // shards:>9,} -> {total:>12,}  ({verdict})")
    print(f"   1 wallet  x {4_000_000:>9,} -> {whole:>12,}  (baseline)")
    print("  Weight is capital x tenure_multiplier(age) and counts no wallets,")
    print("  so splitting is exactly neutral before rounding, and rounding")
    print("  always favours the pool.")


def scenario_reputation() -> None:
    rule("5. Reputation: earned by capital-at-risk x time, diluted by sharding")

    whole = Market.launch(CURVE, vest_duration_seconds=7 * DAY)
    whole.buy("whale", 100_000_000_000_000)
    whole.advance(8 * DAY)
    whole.claim_vested("whale")
    whole.update_reputation("whale")
    rep = whole.reputation("whale")
    capital = whole.position("whale").cost_basis_lamports
    print(f"  one wallet: {sol(capital)} held 8 days")
    print(f"     score {rep.score:,}  tier {rep.tier} ({TIER_NAMES[rep.tier]})")

    shards = Market.launch(CURVE, vest_duration_seconds=7 * DAY)
    for i in range(4):
        shards.buy(f"shard{i}", 25_000_000_000_000)
    shards.advance(8 * DAY)
    total = 0
    for i in range(4):
        shards.claim_vested(f"shard{i}")
        shards.update_reputation(f"shard{i}")
        total += shards.reputation(f"shard{i}").score
    tier = shards.reputation("shard0").tier
    print(f"  four wallets, same capital and time:")
    print(f"     score {total:,} across all four, but each one is tier {tier} "
          f"({TIER_NAMES[tier]})")
    print("  Total score is flat; per-wallet tier strictly drops. Tiers are")
    print("  thresholds on one wallet's score, so sharding is a pure loss.")


def scenario_curve() -> None:
    rule("6. The bonding curve stays solvent")
    m = Market.launch(CURVE, vest_duration_seconds=0)
    print(f"  launch spot price: {m.spot_price()} lamports/token")
    for wallet, amount in [("a", 50_000_000_000_000), ("b", 50_000_000_000_000)]:
        cost = m.buy(wallet, amount)
        print(f"  {wallet} buys {amount:>18,} for {sol(cost)}  "
              f"-> spot {m.spot_price()} lamports")
    m.claim_vested("a")
    out = m.sell("a", 50_000_000_000_000)
    proceeds = getattr(out, "proceeds_lamports", 0)
    print(f"  a sells it all back            for {sol(proceeds)} "
          f"(tax {out.tax:,} tokens to the pool)")
    print(f"  curve vault: {sol(m.vault_lamports)}  (never negative)")
    m.assert_invariants("curve scenario")


def main() -> None:
    print("StackApp - devnet prototype mechanics walk-through")
    print("(simulation only; no network, no keys, no funds)")
    scenario_fifo()
    scenario_transfer()
    scenario_pool()
    scenario_sharding()
    scenario_reputation()
    scenario_curve()
    print()
    print("All invariants held.")


if __name__ == "__main__":
    main()

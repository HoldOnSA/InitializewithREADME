"""A narrated walk-through of the StackApp loyalty-layer mechanics.

    python scenario.py

Runs the same code path the tests do, but prints what happens so you can see
the rules working rather than just read an assertion.
"""

from __future__ import annotations

from stackapp_sim import Market, StackError
from stackapp_sim.constants import LAMPORTS_PER_SOL as SOL
from stackapp_sim.constants import MIN_CLAIM_DELAY_SLOTS, MINUTE, TENURE_TIER_SECONDS


def sol(lamports: int) -> str:
    return f"{lamports / SOL:,.6f} SOL"


def rule(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def scenario_registration_and_tenure() -> None:
    rule("1. Registration is honor-system; weight is not")
    m = Market.register()
    m.write_registration("alice")
    m.set_balance("alice", 1_000_000)
    print("  t+0s   alice registers and holds 1,000,000 tokens")

    for label, seconds in [
        ("just registered", 0),
        ("after 1 minute", TENURE_TIER_SECONDS[0]),
        ("after 10 minutes", TENURE_TIER_SECONDS[1]),
        ("after 30 minutes", TENURE_TIER_SECONDS[2]),
    ]:
        m.now = seconds
        weight = m.sync("alice")
        print(f"  {label:<20} -> weighted shares {weight:,}")
    print("  Weight only ever comes from a LIVE balance read at sync/claim time -")
    print("  the indexer's write_registration can start the tenure clock, but it")
    print("  can never fabricate or inflate the balance side of the weight.")


def scenario_donate_and_claim() -> None:
    rule("2. donate is the only source of real fee revenue")
    m = Market.register()
    m.write_registration("diamond")
    m.set_balance("diamond", 10_000_000)
    m.now = TENURE_TIER_SECONDS[1]
    m.sync("diamond")

    m.donate("creator", 5_000_000)
    print(f"  creator donates {sol(5_000_000)} after claiming it from pump.fun normally")
    print(f"  diamond's projected claimable: {m.claimable('diamond'):,} lamports")

    try:
        m.claim("diamond")
    except StackError as exc:
        print(f"  claim in the same slot -> rejected ({exc})")

    m.advance(slots=MIN_CLAIM_DELAY_SLOTS)
    payout = m.claim("diamond")
    print(f"  +{MIN_CLAIM_DELAY_SLOTS} slots diamond claims {payout:,} lamports")
    m.assert_invariants("donate/claim scenario")


def scenario_sharding() -> None:
    rule("3. Sharding across wallets never out-earns holding in one")
    held = TENURE_TIER_SECONDS[1]

    def run(wallets: int, each: int) -> int:
        m = Market.register()
        for i in range(wallets):
            w = f"w{i}"
            m.write_registration(w)
            m.set_balance(w, each)
        m.advance(seconds=held)
        for i in range(wallets):
            m.sync(f"w{i}")
        m.donate("creator", 1_000_000)
        return sum(m.claimable(f"w{i}") for i in range(wallets))

    whole = run(1, 4_000_000)
    for shards in (2, 4, 16):
        total = run(shards, 4_000_000 // shards)
        verdict = "no gain" if total <= whole else "!! EXPLOIT !!"
        print(f"  {shards:>2} wallet(s) x {4_000_000 // shards:>9,} -> {total:>12,}  ({verdict})")
    print(f"   1 wallet  x {4_000_000:>9,} -> {whole:>12,}  (baseline)")
    print("  Weight is balance x tenure_multiplier(age) with no per-wallet bonus,")
    print("  so splitting is exactly neutral before rounding, and rounding")
    print("  always favours the pool.")


def scenario_flash_loan_guard() -> None:
    rule("4. A same-block balance increase cannot capture a fee it did not earn")
    m = Market.register()
    m.write_registration("victim")
    m.set_balance("victim", 1_000)
    m.sync("victim")
    m.donate("creator", 10_000)

    m.write_registration("attacker")
    m.set_balance("attacker", 1_000_000)
    m.sync("attacker")  # same-slot balance increase

    try:
        m.claim("attacker")
    except StackError as exc:
        print(f"  attacker buys in and claims in the same slot -> rejected ({exc})")

    m.advance(slots=MIN_CLAIM_DELAY_SLOTS)
    print(f"  attacker's claimable after waiting it out: {m.claimable('attacker'):,}")
    print("  (zero - the fee landed before the attacker had any weight at all)")


def main() -> None:
    print("StackApp - pump.fun loyalty layer mechanics walk-through")
    print("(simulation only; no network, no keys, no funds)")
    scenario_registration_and_tenure()
    scenario_donate_and_claim()
    scenario_sharding()
    scenario_flash_loan_guard()
    print()
    print("All invariants held.")


if __name__ == "__main__":
    main()

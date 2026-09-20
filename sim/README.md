# stackapp-sim

A faithful Python mirror of the Anchor program's math and state transitions -
StackApp's loyalty layer on top of real pump.fun tokens.

It exists for two reasons:

1. **The rules can be exercised anywhere Python runs.** No Rust, no Solana CLI,
   no Anchor, no validator, no network, no pip install. The accumulator
   arithmetic and every anti-gaming rule are covered here.
2. **The indexer computes derived numbers with it.** Tenure multiplier and
   projected claimable share on the dashboard come from this package, so the
   UI and the program cannot quietly disagree.

This is a *model*, not the deployed artifact. `programs/stackapp` is the real
thing. `tests/test_parity.py` parses the Rust source and fails if the
constants, PDA seeds, tenure tiers or instruction list drift apart.

## Run it

```bash
python -m unittest discover -s tests -t .   # the suite
python scenario.py                          # narrated walk-through
```

`pytest` also works if you have it (`pip install -e ".[dev]"`), since the tests
are plain `unittest.TestCase` classes.

## Layout

| Module | Mirrors |
|---|---|
| `constants.py` | `src/constants.rs` |
| `math.py` | `src/math/` — accumulator, tenure |
| `state.py` | `src/state/` — `GlobalConfig`, `TokenConfig`, `DepositVault`, `Registration`, `LoyaltyPool` |
| `logic.py` | `src/logic.rs` — the ordered state transitions |
| `market.py` | `src/instructions/` — one `Market` per registered token, plus `assert_invariants()` |

There's no bonding curve, tax curve, vesting or reputation here - StackApp
doesn't run the trade for these tokens (pump.fun does), so there's nothing on
that side for the sim to mirror. `Market.balances` stands in for "the live
SPL balance a `sync`/`claim` would read on chain"; tests move it directly
with `set_balance`, since real balance changes happen entirely outside this
program.

## The invariants

`Market.assert_invariants()` checks all of these at once, and the long-run
test calls it after every one of 400 random operations:

- `pool.total_weighted_shares == sum(registration.weighted_shares)`
- `pool.total_claimed + everything owed <= pool.total_collected` — the pool
  can never become insolvent
- the deposit vault's lamport balance is non-negative

Integer rounding is mirrored exactly — floors where the Rust floors, ceilings
where it ceils — so a Python result and an on-chain result agree bit for bit.

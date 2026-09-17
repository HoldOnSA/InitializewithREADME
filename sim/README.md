# stackapp-sim

A faithful Python mirror of the Anchor program's math and state transitions.

It exists for two reasons:

1. **The rules can be exercised anywhere Python runs.** No Rust, no Solana CLI,
   no Anchor, no validator, no network, no pip install. The accumulator
   arithmetic and every anti-gaming rule are covered here.
2. **The indexer computes derived numbers with it.** Vesting %, current tax rate
   and projected pool share on the dashboard come from this package, so the UI
   and the program cannot quietly disagree.

This is a *model*, not the deployed artifact. `programs/stackapp` is the real
thing. `tests/test_parity.py` parses the Rust source and fails if the constants,
PDA seeds, tenure tiers or instruction list drift apart.

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
| `math.py` | `src/math/` — accumulator, tax curve, vesting, tenure, bonding curve |
| `state.py` | `src/state/` — `Lot`, `Position`, `LoyaltyPool`, `TokenConfig`, `Reputation` |
| `logic.py` | `src/logic.rs` — the ordered state transitions |
| `market.py` | `src/instructions/` — one `Market` per launch, plus `assert_invariants()` |

## The invariants

`Market.assert_invariants()` checks all of these at once, and the long-run test
calls it after every one of 400 random operations:

- `position.spendable == sum(lot.released)` for every wallet
- no lot has `cum_released > original` or `released > original`
- no empty lot survives pruning
- `pool.total_weighted_shares == sum(position.weighted_shares)`
- `pool.total_claimed + everything owed <= pool.total_collected` — the pool can
  never become insolvent
- the curve vault is non-negative and equals `virtual_sol_reserves` minus its
  launch value
- token conservation: `tokens_sold == sum(positions) + (collected - claimed)`

Integer rounding is mirrored exactly — floors where the Rust floors, ceilings
where it ceils — so a Python result and an on-chain result agree bit for bit.

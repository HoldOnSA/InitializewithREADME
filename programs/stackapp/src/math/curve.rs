//! Constant-product bonding curve with virtual reserves.
//!
//! `k = virtual_sol_reserves * virtual_token_reserves` is held constant (and,
//! because of rounding, never allowed to *decrease*). Virtual reserves let the
//! curve start at a sane price without anyone seeding real liquidity.
//!
//! Rounding is always in the curve's favour: buys round the cost **up**, sells
//! round the proceeds **down**. That is what makes the lamport vault provably
//! solvent (see `vault_can_always_pay_out`).

use crate::math::ceil_div;

/// Lamports required to buy `amount` token base-units off the curve.
///
/// Returns `None` if the buy would drain the curve or overflow.
pub fn buy_cost_lamports(
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    amount: u64,
) -> Option<u64> {
    if amount == 0 {
        return Some(0);
    }
    if amount >= virtual_token_reserves {
        return None;
    }
    let k = (virtual_sol_reserves as u128).checked_mul(virtual_token_reserves as u128)?;
    let new_tokens = (virtual_token_reserves - amount) as u128;
    let new_sol = ceil_div(k, new_tokens);
    if new_sol > u64::MAX as u128 {
        return None;
    }
    (new_sol as u64).checked_sub(virtual_sol_reserves)
}

/// Lamports returned for selling `amount` token base-units back to the curve.
pub fn sell_proceeds_lamports(
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    amount: u64,
) -> Option<u64> {
    if amount == 0 {
        return Some(0);
    }
    let k = (virtual_sol_reserves as u128).checked_mul(virtual_token_reserves as u128)?;
    let new_tokens = (virtual_token_reserves as u128).checked_add(amount as u128)?;
    let new_sol = ceil_div(k, new_tokens);
    let new_sol_u64 = if new_sol > u64::MAX as u128 { u64::MAX } else { new_sol as u64 };
    Some(virtual_sol_reserves.saturating_sub(new_sol_u64))
}

/// Spot price in lamports per whole token, given `decimals`.
pub fn spot_price_lamports(
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    decimals: u8,
) -> u64 {
    if virtual_token_reserves == 0 {
        return 0;
    }
    let one_token = 10u128.pow(decimals.min(18) as u32);
    (((virtual_sol_reserves as u128) * one_token) / (virtual_token_reserves as u128))
        .min(u64::MAX as u128) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    // Prototype defaults: 30 SOL virtual, ~1.073e9 whole tokens at 6 decimals.
    const V_SOL: u64 = 30_000_000_000;
    const V_TOK: u64 = 1_073_000_000_000_000;

    fn k(sol: u64, tok: u64) -> u128 {
        (sol as u128) * (tok as u128)
    }

    #[test]
    fn a_buy_always_costs_something() {
        // Even one base unit cannot be free.
        assert!(buy_cost_lamports(V_SOL, V_TOK, 1).unwrap() >= 1);
    }

    #[test]
    fn price_rises_as_supply_is_bought() {
        let chunk = 1_000_000_000_000u64; // 1M tokens
        let first = buy_cost_lamports(V_SOL, V_TOK, chunk).unwrap();
        let mid_sol = V_SOL + first;
        let mid_tok = V_TOK - chunk;
        let second = buy_cost_lamports(mid_sol, mid_tok, chunk).unwrap();
        assert!(second > first, "second chunk {} should cost more than {}", second, first);
    }

    #[test]
    fn buying_out_the_whole_reserve_is_rejected() {
        assert!(buy_cost_lamports(V_SOL, V_TOK, V_TOK).is_none());
        assert!(buy_cost_lamports(V_SOL, V_TOK, V_TOK + 1).is_none());
    }

    #[test]
    fn k_never_decreases_on_a_buy() {
        let before = k(V_SOL, V_TOK);
        let amount = 12_345_678_901u64;
        let cost = buy_cost_lamports(V_SOL, V_TOK, amount).unwrap();
        let after = k(V_SOL + cost, V_TOK - amount);
        assert!(after >= before, "k shrank on buy: {} -> {}", before, after);
    }

    #[test]
    fn k_never_decreases_on_a_sell() {
        // Put the curve in a mid state first.
        let amount = 500_000_000_000u64;
        let cost = buy_cost_lamports(V_SOL, V_TOK, amount).unwrap();
        let (sol, tok) = (V_SOL + cost, V_TOK - amount);

        let before = k(sol, tok);
        let back = 123_456_789u64;
        let proceeds = sell_proceeds_lamports(sol, tok, back).unwrap();
        let after = k(sol - proceeds, tok + back);
        assert!(after >= before, "k shrank on sell: {} -> {}", before, after);
    }

    #[test]
    fn round_trip_never_profits_the_trader() {
        for amount in [1u64, 1_000, 1_000_000_000, 250_000_000_000] {
            let cost = buy_cost_lamports(V_SOL, V_TOK, amount).unwrap();
            let sol = V_SOL + cost;
            let tok = V_TOK - amount;
            let proceeds = sell_proceeds_lamports(sol, tok, amount).unwrap();
            assert!(
                proceeds <= cost,
                "buy/sell round trip of {} paid {} for {} - free money",
                amount,
                proceeds,
                cost
            );
        }
    }

    /// The solvency invariant: real lamports in the vault equal
    /// `virtual_sol_reserves - initial_virtual_sol_reserves`, and that quantity
    /// never goes negative, so a sell can always be paid out.
    #[test]
    fn vault_can_always_pay_out() {
        let mut sol = V_SOL;
        let mut tok = V_TOK;
        let mut vault: i128 = 0;
        let mut outstanding: u64 = 0;

        let mut seed: u64 = 0xC0FFEE;
        let mut next = move || {
            seed = seed
                .wrapping_mul(6_364_136_223_846_793_005)
                .wrapping_add(1_442_695_040_888_963_407);
            seed >> 33
        };

        for step in 0..600u64 {
            let buy = step % 3 != 2 || outstanding == 0;
            if buy {
                let amount = 1 + next() % 5_000_000_000;
                let cost = match buy_cost_lamports(sol, tok, amount) {
                    Some(c) => c,
                    None => continue,
                };
                sol += cost;
                tok -= amount;
                outstanding += amount;
                vault += cost as i128;
            } else {
                let amount = 1 + next() % outstanding;
                let proceeds = sell_proceeds_lamports(sol, tok, amount).unwrap();
                assert!(
                    (proceeds as i128) <= vault,
                    "step {}: sell wanted {} lamports but vault holds {}",
                    step,
                    proceeds,
                    vault
                );
                sol -= proceeds;
                tok += amount;
                outstanding -= amount;
                vault -= proceeds as i128;
            }
            assert!(vault >= 0, "vault went negative at step {}", step);
            assert_eq!(
                vault as u64,
                sol - V_SOL,
                "vault drifted from the virtual reserve delta at step {}",
                step
            );
            assert_eq!(outstanding, V_TOK - tok, "token accounting drifted at step {}", step);
        }
    }

    #[test]
    fn spot_price_is_sane_at_launch() {
        // 30 SOL / 1.073e9 tokens ~= 28 lamports per whole token.
        let p = spot_price_lamports(V_SOL, V_TOK, 6);
        assert!(p >= 20 && p <= 40, "launch spot price was {} lamports", p);
        assert_eq!(spot_price_lamports(V_SOL, 0, 6), 0);
    }

    #[test]
    fn spot_price_rises_with_supply_sold() {
        let amount = 500_000_000_000_000u64;
        let cost = buy_cost_lamports(V_SOL, V_TOK, amount).unwrap();
        let before = spot_price_lamports(V_SOL, V_TOK, 6);
        let after = spot_price_lamports(V_SOL + cost, V_TOK - amount, 6);
        assert!(after > before);
    }
}

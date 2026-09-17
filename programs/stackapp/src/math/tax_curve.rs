//! Time-decaying sell tax.

use anchor_lang::prelude::*;

use crate::constants::{BPS_DENOMINATOR, MAX_TAX_BPS, MAX_TAX_CURVE_POINTS};
use crate::errors::StackError;
use crate::math::ceil_div;
use crate::state::TaxPoint;

/// Validate a tax curve at launch time.
///
/// Rules:
/// * non-empty, and no longer than `MAX_TAX_CURVE_POINTS`
/// * first point is at `seconds_held == 0` so every age maps to a rate
/// * `seconds_held` strictly increasing
/// * `tax_bps` non-increasing, so holding longer can never cost more (a curve
///   that ever rose would create a "sell now before the tax goes up" cliff,
///   which is the opposite of what this launchpad is for)
/// * every rate at or below `MAX_TAX_BPS`
pub fn validate_tax_curve(points: &[TaxPoint]) -> Result<()> {
    require!(!points.is_empty(), StackError::EmptyTaxCurve);
    require!(points.len() <= MAX_TAX_CURVE_POINTS, StackError::TaxCurveTooLong);
    require!(points[0].seconds_held == 0, StackError::TaxCurveMustStartAtZero);

    for i in 0..points.len() {
        require!(points[i].tax_bps <= MAX_TAX_BPS, StackError::TaxRateTooHigh);
        if i > 0 {
            require!(
                points[i].seconds_held > points[i - 1].seconds_held,
                StackError::TaxCurveNotSorted
            );
            require!(
                points[i].tax_bps <= points[i - 1].tax_bps,
                StackError::TaxCurveNotMonotonic
            );
        }
    }
    Ok(())
}

/// The tax rate that applies to a lot of the given age.
///
/// Step function: the rate of the last point whose `seconds_held <= age`.
/// A negative age (clock skew) is treated as zero, i.e. the harshest rate.
pub fn tax_bps_for_age(points: &[TaxPoint], age_seconds: i64) -> u16 {
    if points.is_empty() {
        return 0;
    }
    let age = if age_seconds < 0 { 0 } else { age_seconds };
    let mut rate = points[0].tax_bps;
    for p in points.iter() {
        if p.seconds_held <= age {
            rate = p.tax_bps;
        } else {
            break;
        }
    }
    rate
}

/// Tax owed on `amount` at `bps`, rounded **up**.
///
/// Rounding up is deliberate and is an anti-gaming rule in its own right: with
/// floor rounding, an exit split into many sub-`10_000/bps` dust sells would
/// pay zero tax. Rounding up makes splitting weakly worse, never better.
pub fn tax_for_amount(amount: u64, bps: u16) -> u64 {
    if amount == 0 || bps == 0 {
        return 0;
    }
    let tax = ceil_div((amount as u128) * (bps as u128), BPS_DENOMINATOR as u128);
    tax.min(amount as u128) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn curve() -> Vec<TaxPoint> {
        vec![
            TaxPoint { seconds_held: 0, tax_bps: 3_000 },
            TaxPoint { seconds_held: 3_600, tax_bps: 2_000 },
            TaxPoint { seconds_held: 86_400, tax_bps: 1_000 },
            TaxPoint { seconds_held: 604_800, tax_bps: 0 },
        ]
    }

    #[test]
    fn rate_is_a_descending_step_function() {
        let c = curve();
        assert_eq!(tax_bps_for_age(&c, 0), 3_000);
        assert_eq!(tax_bps_for_age(&c, 3_599), 3_000);
        assert_eq!(tax_bps_for_age(&c, 3_600), 2_000);
        assert_eq!(tax_bps_for_age(&c, 86_399), 2_000);
        assert_eq!(tax_bps_for_age(&c, 86_400), 1_000);
        assert_eq!(tax_bps_for_age(&c, 604_800), 0);
        assert_eq!(tax_bps_for_age(&c, i64::MAX), 0);
    }

    #[test]
    fn negative_age_pays_the_top_rate() {
        assert_eq!(tax_bps_for_age(&curve(), -10_000), 3_000);
    }

    #[test]
    fn valid_curve_is_accepted() {
        assert!(validate_tax_curve(&curve()).is_ok());
    }

    #[test]
    fn curve_must_start_at_zero() {
        let c = vec![TaxPoint { seconds_held: 60, tax_bps: 1_000 }];
        assert!(validate_tax_curve(&c).is_err());
    }

    #[test]
    fn rising_curve_is_rejected() {
        let c = vec![
            TaxPoint { seconds_held: 0, tax_bps: 1_000 },
            TaxPoint { seconds_held: 3_600, tax_bps: 2_000 },
        ];
        assert!(
            validate_tax_curve(&c).is_err(),
            "a rising curve would reward selling earlier"
        );
    }

    #[test]
    fn unsorted_curve_is_rejected() {
        let c = vec![
            TaxPoint { seconds_held: 0, tax_bps: 3_000 },
            TaxPoint { seconds_held: 3_600, tax_bps: 2_000 },
            TaxPoint { seconds_held: 3_600, tax_bps: 1_000 },
        ];
        assert!(validate_tax_curve(&c).is_err());
    }

    #[test]
    fn honeypot_rate_is_rejected() {
        let c = vec![TaxPoint { seconds_held: 0, tax_bps: 10_000 }];
        assert!(validate_tax_curve(&c).is_err());
    }

    #[test]
    fn empty_curve_is_rejected() {
        assert!(validate_tax_curve(&[]).is_err());
    }

    #[test]
    fn tax_rounds_up_so_dust_sells_cannot_dodge_it() {
        // 30% of 1 unit floors to 0 but ceils to 1.
        assert_eq!(tax_for_amount(1, 3_000), 1);
        assert_eq!(tax_for_amount(10, 3_000), 3);
        assert_eq!(tax_for_amount(11, 3_000), 4); // 3.3 -> 4
        assert_eq!(tax_for_amount(0, 3_000), 0);
        assert_eq!(tax_for_amount(1_000, 0), 0);
    }

    /// Splitting one exit into many small exits must never reduce total tax.
    #[test]
    fn splitting_an_exit_never_reduces_tax() {
        let bps = 3_000u16;
        for total in [1u64, 7, 99, 1_000, 123_457] {
            let single = tax_for_amount(total, bps);
            for chunk in [1u64, 2, 3, 7, 13] {
                let mut remaining = total;
                let mut split_tax = 0u64;
                while remaining > 0 {
                    let take = chunk.min(remaining);
                    split_tax += tax_for_amount(take, bps);
                    remaining -= take;
                }
                assert!(
                    split_tax >= single,
                    "total {} chunk {}: split tax {} < single tax {}",
                    total,
                    chunk,
                    split_tax,
                    single
                );
            }
        }
    }

    #[test]
    fn tax_never_exceeds_the_amount() {
        assert_eq!(tax_for_amount(u64::MAX, MAX_TAX_BPS), {
            let t = ceil_div((u64::MAX as u128) * (MAX_TAX_BPS as u128), 10_000);
            t.min(u64::MAX as u128) as u64
        });
        assert!(tax_for_amount(5, 9_000) <= 5);
    }
}

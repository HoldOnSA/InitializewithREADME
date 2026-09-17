//! Linear vesting of a purchase lot.

use crate::constants::BPS_DENOMINATOR;

/// How much of `original` should be unlocked by `now`.
///
/// Linear from `buy_timestamp` to `buy_timestamp + vest_duration_seconds`.
/// A zero (or negative) duration means the lot is liquid immediately.
/// Rounds **down**, so the schedule never runs ahead of itself.
pub fn vested_amount(original: u64, buy_timestamp: i64, now: i64, vest_duration_seconds: i64) -> u64 {
    if original == 0 {
        return 0;
    }
    if vest_duration_seconds <= 0 {
        return original;
    }
    if now <= buy_timestamp {
        return 0;
    }
    let elapsed = (now - buy_timestamp) as u128;
    let duration = vest_duration_seconds as u128;
    if elapsed >= duration {
        return original;
    }
    (((original as u128) * elapsed) / duration) as u64
}

/// Vesting progress in basis points, for display.
pub fn vested_bps(buy_timestamp: i64, now: i64, vest_duration_seconds: i64) -> u64 {
    if vest_duration_seconds <= 0 {
        return BPS_DENOMINATOR;
    }
    if now <= buy_timestamp {
        return 0;
    }
    let elapsed = (now - buy_timestamp) as u128;
    let duration = vest_duration_seconds as u128;
    if elapsed >= duration {
        return BPS_DENOMINATOR;
    }
    ((elapsed * BPS_DENOMINATOR as u128) / duration) as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    const DAY: i64 = 86_400;

    #[test]
    fn nothing_is_vested_at_purchase() {
        assert_eq!(vested_amount(1_000, 100, 100, 10 * DAY), 0);
        assert_eq!(vested_amount(1_000, 100, 50, 10 * DAY), 0);
    }

    #[test]
    fn vesting_is_linear() {
        let dur = 10 * DAY;
        assert_eq!(vested_amount(1_000, 0, DAY, dur), 100);
        assert_eq!(vested_amount(1_000, 0, 5 * DAY, dur), 500);
        assert_eq!(vested_amount(1_000, 0, 9 * DAY, dur), 900);
    }

    #[test]
    fn fully_vested_at_and_after_the_deadline() {
        let dur = 10 * DAY;
        assert_eq!(vested_amount(1_000, 0, dur, dur), 1_000);
        assert_eq!(vested_amount(1_000, 0, 1_000 * DAY, dur), 1_000);
    }

    #[test]
    fn zero_duration_is_immediately_liquid() {
        assert_eq!(vested_amount(1_000, 0, 0, 0), 1_000);
    }

    #[test]
    fn vesting_is_monotonic_and_never_overshoots() {
        let dur = 7 * DAY;
        let original = 1_000_003u64;
        let mut prev = 0u64;
        for t in 0..(9 * DAY / 977) {
            let now = t * 977;
            let v = vested_amount(original, 0, now, dur);
            assert!(v >= prev, "vesting went backwards at t={}", now);
            assert!(v <= original, "vesting overshot at t={}", now);
            prev = v;
        }
        assert_eq!(prev, original);
    }

    #[test]
    fn bps_tracks_amount() {
        let dur = 4 * DAY;
        assert_eq!(vested_bps(0, 0, dur), 0);
        assert_eq!(vested_bps(0, DAY, dur), 2_500);
        assert_eq!(vested_bps(0, 2 * DAY, dur), 5_000);
        assert_eq!(vested_bps(0, 10 * DAY, dur), 10_000);
        assert_eq!(vested_bps(0, 123, 0), 10_000);
    }
}

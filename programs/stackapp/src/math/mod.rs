//! Pure, side-effect-free math.
//!
//! Nothing in this module touches `Clock`, accounts, or CPI, so every function
//! here is exercised by plain `cargo test` on the host with no validator and no
//! BPF toolchain.

pub mod accumulator;
pub mod curve;
pub mod tax_curve;
pub mod tenure;
pub mod vesting;

pub use accumulator::*;
pub use curve::*;
pub use tax_curve::*;
pub use tenure::*;
pub use vesting::*;

/// Ceiling division for u128. Returns 0 when `b == 0`.
#[inline]
pub fn ceil_div(a: u128, b: u128) -> u128 {
    if b == 0 {
        return 0;
    }
    let q = a / b;
    if a % b == 0 {
        q
    } else {
        q + 1
    }
}

#[cfg(test)]
mod tests {
    use super::ceil_div;

    #[test]
    fn ceil_div_rounds_up() {
        assert_eq!(ceil_div(0, 3), 0);
        assert_eq!(ceil_div(1, 3), 1);
        assert_eq!(ceil_div(3, 3), 1);
        assert_eq!(ceil_div(4, 3), 2);
        assert_eq!(ceil_div(5, 0), 0);
    }
}

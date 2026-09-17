use anchor_lang::prelude::*;

use crate::constants::*;

/// One point on a step-shaped sell-tax curve.
///
/// A point `{ seconds_held, tax_bps }` means: "from `seconds_held` onward (and
/// until the next point), the tax is `tax_bps`". The curve is validated to be
/// non-increasing in `tax_bps`, so holding longer can never cost more.
#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, Debug, Default, PartialEq, Eq, InitSpace)]
pub struct TaxPoint {
    pub seconds_held: i64,
    pub tax_bps: u16,
}

/// Per-mint launch configuration. PDA: `["config", mint]`.
#[account]
#[derive(InitSpace, Debug)]
pub struct TokenConfig {
    pub mint: Pubkey,
    pub creator: Pubkey,
    pub launch_timestamp: i64,
    pub vest_duration_seconds: i64,
    /// The program's own pool address. This is the only transfer destination
    /// exempt from the sell tax (see `donate_to_pool`).
    pub pool_account: Pubkey,
    /// Lamport vault backing the bonding curve. PDA: `["curve_vault", mint]`.
    pub curve_vault: Pubkey,
    #[max_len(MAX_TAX_CURVE_POINTS)]
    pub tax_curve: Vec<TaxPoint>,

    // --- bonding curve state (constant product, virtual reserves) ---
    pub virtual_sol_reserves: u64,
    pub virtual_token_reserves: u64,
    /// Lamports actually held by `curve_vault` on behalf of the curve.
    pub real_sol_reserves: u64,
    /// Net tokens currently in circulation via this curve.
    pub tokens_sold: u64,

    // --- lifetime counters, cheap for the indexer/UI ---
    pub total_buy_volume_tokens: u64,
    pub total_sell_volume_tokens: u64,
    pub holder_count: u32,

    pub decimals: u8,
    pub bump: u8,
    pub curve_vault_bump: u8,
}

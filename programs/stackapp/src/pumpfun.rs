//! CPI into pump.fun's real, live `distribute_creator_fees` instruction.
//!
//! StackApp never becomes a `SharingConfig` authority and never touches a
//! creator's admin key (see `SECURITY_NOTES.md`). The only thing this module
//! assumes is that a creator has already, on their own, called pump.fun's
//! own `update_fee_shares_v2` to add `DepositVault` as one of their token's
//! up to 10 shareholders - a plain pubkey in `SharingConfig.shareholders`,
//! with no owner-type or signer requirement on it at all. `pull_pump_fee`
//! (see `instructions/pull_pump_fee.rs`) is the permissionless crank that
//! then collects whatever share has accrued to that address.
//!
//! Verified against real, live mainnet state before writing any of this
//! (read-only RPC, no wallet or transactions involved):
//!
//! - Decoded pump_fees's live `SharingConfig` accounts directly and found
//!   real shareholder addresses owned by other on-chain programs, not just
//!   wallets - `Shareholder.address` is a bare `pubkey` with no on-chain
//!   constraint requiring it to be a wallet.
//! - Found a real, finalized transaction
//!   (`2S2jNdkyWr2qSoYgrPs4AWaVK1qgPmYVFnmZfmweZtWEE7WHckSWwSWruqxH6wVi4aaV5FGN6sQxxzASfXxdc6HN`)
//!   where `distribute_creator_fees` moved 303,556 lamports directly into
//!   such a program-owned PDA via an inner `system_program::transfer`, with
//!   the PDA appearing as `signer: false` throughout - proof a
//!   `DepositVault`-shaped account can sit in the shareholder slot and
//!   actually get paid, not just be listed.
//! - Confirmed the exact instruction used by comparing that transaction's
//!   raw instruction data, byte for byte, against
//!   `sha256("global:distribute_creator_fees")[:8]` -
//!   `[165, 114, 103, 0, 121, 206, 247, 81]` - which is the **v1**
//!   instruction, not `distribute_creator_fees_v2`. v1 takes no args, needs
//!   no `payer`/quote-mint/ATA accounts at all (pump.fun's own IDL docs:
//!   "Distributes creator fees to shareholders based on their share
//!   percentages"), and is what StackApp uses here - v2 exists to support
//!   non-SOL quote mints via on-the-fly ATA creation, which StackApp has no
//!   use for.
//! - Cross-checked the exact `remaining_accounts` convention against
//!   pump.fun's own published docs
//!   (`pump-fun/pump-public-docs`, `docs/instructions/CREATOR_FEE_SHARING.md`):
//!   for a wrapped-SOL quote, `remaining_accounts` is
//!   `[shareholder_1, ..., shareholder_N]`, in exactly `sharing_config`'s
//!   own order, and each shareholder is paid lamports directly - no ATA
//!   involved. The v1 IDL's own account list and seed metadata
//!   (`idl/pump.json`) supplied the exact fixed-account order and PDA seeds
//!   below.

use anchor_lang::prelude::*;
use anchor_lang::solana_program::instruction::{AccountMeta, Instruction};

use crate::errors::StackError;

/// The main pump.fun program - owns bonding curves, creator vaults, and
/// `distribute_creator_fees`.
pub const PUMP_PROGRAM_ID: Pubkey = pubkey!("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");

/// pump.fun's Fee Sharing program - owns `SharingConfig` and
/// `update_fee_shares_v2`. StackApp never signs for anything on this
/// program; it only ever reads and forwards a `SharingConfig` address that a
/// creator configured on their own.
pub const PUMP_FEES_PROGRAM_ID: Pubkey = pubkey!("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ");

/// `sha256("global:distribute_creator_fees")[:8]` - Anchor's standard global
/// instruction discriminator. Verified against a real transaction's raw
/// instruction data (see module docs), not just computed from the name.
pub const DISTRIBUTE_CREATOR_FEES_DISCRIMINATOR: [u8; 8] =
    [165, 114, 103, 0, 121, 206, 247, 81];

pub const SEED_SHARING_CONFIG: &[u8] = b"sharing-config"; // under PUMP_FEES_PROGRAM_ID, [seed, mint]
pub const SEED_BONDING_CURVE: &[u8] = b"bonding-curve"; // under PUMP_PROGRAM_ID, [seed, mint]
pub const SEED_CREATOR_VAULT: &[u8] = b"creator-vault"; // under PUMP_PROGRAM_ID, [seed, bonding_curve.creator]
pub const SEED_EVENT_AUTHORITY: &[u8] = b"__event_authority"; // under PUMP_PROGRAM_ID, fixed

/// Build the raw `distribute_creator_fees` CPI instruction.
///
/// `shareholders` must be every address in `sharing_config.shareholders`,
/// in that exact order - not a subset. The instruction pays out the whole
/// config in one call; there is no way to ask it to pay just one
/// shareholder (see `pull_pump_fee`'s handler for why `DepositVault` must
/// still be checked as present in this list before calling this).
///
/// `creator_vault`'s real derivation is `[b"creator-vault",
/// bonding_curve.creator]` (per pump.fun's own IDL) - the caller is
/// responsible for passing the address already derived against
/// `sharing_config`'s own key, which is only correct once `mint` has
/// actually been migrated into fee-sharing (`bonding_curve.creator ==
/// sharing_config`). If it hasn't, this CPI simply fails - pump.fun's own
/// program independently re-derives and checks this account, so there is no
/// way for a wrong guess here to move anyone's funds incorrectly.
pub fn distribute_creator_fees_ix(
    mint: Pubkey,
    bonding_curve: Pubkey,
    sharing_config: Pubkey,
    creator_vault: Pubkey,
    event_authority: Pubkey,
    shareholders: &[Pubkey],
) -> Instruction {
    let mut accounts = vec![
        AccountMeta::new_readonly(mint, false),
        AccountMeta::new_readonly(bonding_curve, false),
        AccountMeta::new_readonly(sharing_config, false),
        AccountMeta::new(creator_vault, false),
        AccountMeta::new_readonly(anchor_lang::solana_program::system_program::ID, false),
        AccountMeta::new_readonly(event_authority, false),
        AccountMeta::new_readonly(PUMP_PROGRAM_ID, false),
    ];
    accounts.extend(shareholders.iter().map(|s| AccountMeta::new(*s, false)));

    Instruction {
        program_id: PUMP_PROGRAM_ID,
        accounts,
        data: DISTRIBUTE_CREATOR_FEES_DISCRIMINATOR.to_vec(),
    }
}

/// Byte offset of `SharingConfig.shareholders`' length prefix, past the
/// 8-byte discriminator and `bump(1) version(1) status(1) mint(32)
/// admin(32) admin_revoked(1)` fields this check doesn't need. Each entry
/// past that is `address(32) share_bps(2)` - see `pump_fees`'s IDL
/// (`Shareholder`/`SharingConfig` type definitions).
const SHAREHOLDERS_LEN_OFFSET: usize = 8 + 1 + 1 + 1 + 32 + 32 + 1;
const SHAREHOLDER_ENTRY_LEN: usize = 32 + 2;

/// Decode just `SharingConfig.shareholders`' addresses, in order, skipping
/// `share_bps` (this check only needs identity and order, not proportions).
fn decode_shareholder_addresses(sharing_config_data: &[u8]) -> Result<Vec<Pubkey>> {
    require!(
        sharing_config_data.len() >= SHAREHOLDERS_LEN_OFFSET + 4,
        StackError::MalformedSharingConfig
    );

    let mut offset = SHAREHOLDERS_LEN_OFFSET;
    let count = u32::from_le_bytes(
        sharing_config_data[offset..offset + 4]
            .try_into()
            .map_err(|_| StackError::MalformedSharingConfig)?,
    ) as usize;
    offset += 4;

    let mut addresses = Vec::with_capacity(count);
    for _ in 0..count {
        require!(
            offset + SHAREHOLDER_ENTRY_LEN <= sharing_config_data.len(),
            StackError::MalformedSharingConfig
        );
        let mut key_bytes = [0u8; 32];
        key_bytes.copy_from_slice(&sharing_config_data[offset..offset + 32]);
        addresses.push(Pubkey::from(key_bytes));
        offset += SHAREHOLDER_ENTRY_LEN;
    }
    Ok(addresses)
}

/// Confirm `remaining_account_keys` is exactly `sharing_config_data`'s own
/// `shareholders` list, in the same order - defense in depth on top of
/// whatever validation pump.fun's own `distribute_creator_fees` handler
/// does internally. That program's source isn't public (only its IDL and
/// docs are - see `SECURITY_NOTES.md`), so its exact enforcement can't be
/// independently confirmed; StackApp checks this itself rather than
/// trusting the caller's list, or pump.fun's, blindly. Called before the
/// CPI is ever built, so a mismatched list is rejected without pump.fun's
/// program - or anyone's real funds - being touched at all.
pub fn assert_matches_on_chain_shareholders(
    sharing_config_data: &[u8],
    remaining_account_keys: &[Pubkey],
) -> Result<()> {
    let expected = decode_shareholder_addresses(sharing_config_data)?;
    require!(
        remaining_account_keys.len() == expected.len()
            && remaining_account_keys.iter().eq(expected.iter()),
        StackError::ShareholderListMismatch
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    /// Real `SharingConfig` account bytes for
    /// `13m97yCqejk5CJbnSq8xLs1NiYzcPjaEfDczvZNnRLgH` - the config behind the
    /// verified mainnet payout transaction
    /// `2S2jNdkyWr2qSoYgrPs4AWaVK1qgPmYVFnmZfmweZtWEE7WHckSWwSWruqxH6wVi4aaV5FGN6sQxxzASfXxdc6HN`
    /// cited in this module's doc comment. Fetched fresh via `getAccountInfo`
    /// on 2026-09-23 and trimmed of the account's trailing zero padding (it's
    /// pre-allocated to `SharingConfig::MAX_SIZE` = 1024 bytes; only the
    /// first 114 are ever written). A real regression fixture, not
    /// synthetic - this is what keeps `SHAREHOLDERS_LEN_OFFSET`/
    /// `SHAREHOLDER_ENTRY_LEN` checked against actual chain data going
    /// forward, not just against this file's own `encode_sharing_config`.
    const REAL_SHARING_CONFIG_HEX: &str = "d84a0900388c5d4bfc02015150c210c8e88c33fbccdae6a74d3819ba6f544ef75f70cf7b99e421abb210252b4a1ffbd9dc3b0a4e9cbbe8e3fbecbc038109c93f917ab7f424ae6b9e0be835010100000000a64e8aedb9100401843405411b1a3d89d80f9ad5e4abe82d359d7cd06b446b1027";

    fn decode_hex(s: &str) -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
            .collect()
    }

    #[test]
    fn decodes_a_real_mainnet_sharing_configs_shareholder_correctly() {
        let data = decode_hex(REAL_SHARING_CONFIG_HEX);
        let addresses = decode_shareholder_addresses(&data).unwrap();
        assert_eq!(
            addresses,
            vec![Pubkey::from_str("13Y5np367TRpLqpXrVWAqriUs57DiMMULmEMBa3KtYsc").unwrap()]
        );
    }

    /// Build a byte buffer matching `SharingConfig`'s real on-chain layout,
    /// for the given (address, share_bps) shareholders. Header field values
    /// besides `shareholders` don't matter to this check, so they're zeroed.
    fn encode_sharing_config(shareholders: &[(Pubkey, u16)]) -> Vec<u8> {
        let mut data = vec![0u8; SHAREHOLDERS_LEN_OFFSET];
        data.extend_from_slice(&(shareholders.len() as u32).to_le_bytes());
        for (address, share_bps) in shareholders {
            data.extend_from_slice(address.as_ref());
            data.extend_from_slice(&share_bps.to_le_bytes());
        }
        data
    }

    #[test]
    fn a_matching_list_in_order_is_accepted() {
        let a = Pubkey::new_unique();
        let b = Pubkey::new_unique();
        let data = encode_sharing_config(&[(a, 5_000), (b, 5_000)]);

        assert!(assert_matches_on_chain_shareholders(&data, &[a, b]).is_ok());
    }

    #[test]
    fn a_deliberately_mismatched_list_is_rejected_before_any_cpi() {
        let a = Pubkey::new_unique();
        let b = Pubkey::new_unique();
        let attacker = Pubkey::new_unique();
        let data = encode_sharing_config(&[(a, 5_000), (b, 5_000)]);

        // Wrong address at a real position.
        assert!(assert_matches_on_chain_shareholders(&data, &[a, attacker]).is_err());
        // Right addresses, wrong order.
        assert!(assert_matches_on_chain_shareholders(&data, &[b, a]).is_err());
        // Missing an entry entirely.
        assert!(assert_matches_on_chain_shareholders(&data, &[a]).is_err());
        // An extra, unexpected entry appended.
        assert!(assert_matches_on_chain_shareholders(&data, &[a, b, attacker]).is_err());
    }

    #[test]
    fn truncated_account_data_is_rejected_as_malformed_rather_than_panicking() {
        let a = Pubkey::new_unique();
        let mut data = encode_sharing_config(&[(a, 10_000)]);
        data.truncate(data.len() - 1); // cut off mid-entry

        assert!(assert_matches_on_chain_shareholders(&data, &[a]).is_err());
    }
}

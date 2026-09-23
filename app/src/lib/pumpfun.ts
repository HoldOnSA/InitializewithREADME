/**
 * Client-side reads of pump.fun's own on-chain state, plus the `pull_pump_fee`
 * instruction builder.
 *
 * Mirrors `programs/stackapp/src/pumpfun.rs` exactly - same program IDs, same
 * PDA seeds, same `SharingConfig` byte layout, same v1 (not `_v2`)
 * `distribute_creator_fees` discriminator. See that file's doc comment for
 * the real-mainnet verification this is all built against: a real,
 * finalized payout transaction, a real `SharingConfig` account fetched fresh
 * and decoded byte-for-byte, and pump.fun's own published docs for the
 * `remaining_accounts` convention (every shareholder, in `SharingConfig`'s
 * own order - not a caller-chosen subset).
 *
 * StackApp never becomes a `SharingConfig` authority and never touches a
 * creator's admin key here either - this only ever reads an address a
 * creator configured on their own via pump.fun's `update_fee_shares_v2`.
 */

import type { Connection } from "@solana/web3.js";
import { PublicKey, SystemProgram, TransactionInstruction } from "@solana/web3.js";

import { depositVaultPda, discriminator, loyaltyPoolPda, PROGRAM_ID } from "./program";

export const PUMP_PROGRAM_ID = new PublicKey("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");
export const PUMP_FEES_PROGRAM_ID = new PublicKey(
  "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
);

const SEED_SHARING_CONFIG = new TextEncoder().encode("sharing-config");
const SEED_BONDING_CURVE = new TextEncoder().encode("bonding-curve");
const SEED_CREATOR_VAULT = new TextEncoder().encode("creator-vault");
const SEED_EVENT_AUTHORITY = new TextEncoder().encode("__event_authority");

/** `sha256("global:distribute_creator_fees")[:8]` - the v1 instruction, not
 * `_v2`. Verified byte-for-byte against a real transaction's raw instruction
 * data - see `pumpfun.rs`. */
const DISTRIBUTE_CREATOR_FEES_DISCRIMINATOR = discriminator("distribute_creator_fees");

export function sharingConfigPda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync(
    [SEED_SHARING_CONFIG, mint.toBytes()],
    PUMP_FEES_PROGRAM_ID
  );
}

export function bondingCurvePda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_BONDING_CURVE, mint.toBytes()], PUMP_PROGRAM_ID);
}

/** Real seeds are `[b"creator-vault", bonding_curve.creator]`; correct here
 * only once `mint` is actually migrated into fee-sharing
 * (`bonding_curve.creator == sharingConfig`) - see `pumpfun.rs`. */
export function creatorVaultPda(sharingConfig: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync(
    [SEED_CREATOR_VAULT, sharingConfig.toBytes()],
    PUMP_PROGRAM_ID
  );
}

export function pumpEventAuthorityPda(): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_EVENT_AUTHORITY], PUMP_PROGRAM_ID);
}

export type Shareholder = { address: PublicKey; shareBps: number };

export type DecodedSharingConfig = {
  adminRevoked: boolean;
  shareholders: Shareholder[];
};

const HEADER_LEN = 8 + 1 + 1 + 1 + 32 + 32; // disc, bump, version, status, mint, admin
const ADMIN_REVOKED_OFFSET = HEADER_LEN;
const SHAREHOLDERS_LEN_OFFSET = HEADER_LEN + 1; // + admin_revoked(1)
const SHAREHOLDER_ENTRY_LEN = 32 + 2;

/** Decode the two `SharingConfig` fields this app needs. Mirrors
 * `pumpfun.rs::decode_shareholder_addresses` exactly - same offsets, same
 * real-mainnet-verified layout. Returns `null` on data too short to be a
 * real `SharingConfig` rather than throwing, since a not-yet-created config
 * is an expected, routine state (see `sharingConfigStatus`). */
export function decodeSharingConfig(data: Buffer): DecodedSharingConfig | null {
  if (data.length < SHAREHOLDERS_LEN_OFFSET + 4) return null;

  const adminRevoked = data[ADMIN_REVOKED_OFFSET] !== 0;
  const count = data.readUInt32LE(SHAREHOLDERS_LEN_OFFSET);

  let offset = SHAREHOLDERS_LEN_OFFSET + 4;
  const shareholders: Shareholder[] = [];
  for (let i = 0; i < count; i += 1) {
    if (offset + SHAREHOLDER_ENTRY_LEN > data.length) return null;
    shareholders.push({
      address: new PublicKey(data.subarray(offset, offset + 32)),
      shareBps: data.readUInt16LE(offset + 32),
    });
    offset += SHAREHOLDER_ENTRY_LEN;
  }
  return { adminRevoked, shareholders };
}

export async function fetchSharingConfig(
  connection: Connection,
  mint: PublicKey
): Promise<DecodedSharingConfig | null> {
  const [address] = sharingConfigPda(mint);
  const info = await connection.getAccountInfo(address, "confirmed");
  if (!info) return null;
  return decodeSharingConfig(info.data);
}

/**
 * Where a token stands on the path to an automatic pump.fun fee pull -
 * see the setup-flow states this drives in `token/[mint]/page.tsx`.
 *
 * `admin_revoked` is the load-bearing field: pump.fun's own
 * `update_fee_shares_v2` sets the final shareholder list and revokes further
 * admin updates *in the same call* - it can only ever be called once per
 * config (confirmed in pump.fun's own docs). So once `adminRevoked` is true,
 * the shareholder list is permanently frozen: either `DepositVault` is in it
 * (pulls work, forever) or it isn't (pulls can never work for this token,
 * forever - `donate` is the only path left).
 */
export type PumpFeeSetupStatus =
  | { state: "not-configured" } // no SharingConfig yet, or not yet finalized - keep polling
  | { state: "permanently-unavailable" } // finalized without DepositVault - stop polling
  | { state: "ready"; shareholders: Shareholder[] }; // finalized with DepositVault - pulls work

export function pumpFeeSetupStatus(
  config: DecodedSharingConfig | null,
  depositVault: PublicKey
): PumpFeeSetupStatus {
  if (!config || !config.adminRevoked) return { state: "not-configured" };
  const included = config.shareholders.some((s) => s.address.equals(depositVault));
  return included
    ? { state: "ready", shareholders: config.shareholders }
    : { state: "permanently-unavailable" };
}

/** CPI into pump.fun's real `distribute_creator_fees` (v1), then inline the
 * same reconcile logic `reconcile.rs` uses to fold the proceeds into the
 * pool - see `instructions/pull_pump_fee.rs`. `shareholders` must be every
 * address currently in `mint`'s `SharingConfig.shareholders`, in that exact
 * order - not a subset (pump.fun pays the whole config in one call). */
export function pullPumpFeeIx(params: {
  cranker: PublicKey;
  mint: PublicKey;
  shareholders: PublicKey[];
}): TransactionInstruction {
  const [pool] = loyaltyPoolPda(params.mint);
  const [vault] = depositVaultPda(params.mint);
  const [sharingConfig] = sharingConfigPda(params.mint);
  const [bondingCurve] = bondingCurvePda(params.mint);
  const [creatorVault] = creatorVaultPda(sharingConfig);
  const [eventAuthority] = pumpEventAuthorityPda();

  // Order must match `PullPumpFee`'s Rust struct field order exactly, then
  // every current shareholder as remaining accounts, in `SharingConfig`'s
  // own order - see this file's and `pumpfun.rs`'s doc comments.
  const keys = [
    { pubkey: params.cranker, isSigner: true, isWritable: false },
    { pubkey: params.mint, isSigner: false, isWritable: false },
    { pubkey: pool, isSigner: false, isWritable: true },
    { pubkey: vault, isSigner: false, isWritable: true },
    { pubkey: sharingConfig, isSigner: false, isWritable: false },
    { pubkey: bondingCurve, isSigner: false, isWritable: false },
    { pubkey: creatorVault, isSigner: false, isWritable: true },
    { pubkey: eventAuthority, isSigner: false, isWritable: false },
    { pubkey: PUMP_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    ...params.shareholders.map((address) => ({
      pubkey: address,
      isSigner: false,
      isWritable: true,
    })),
  ];

  return new TransactionInstruction({
    programId: PROGRAM_ID,
    keys,
    data: Buffer.from(discriminator("pull_pump_fee")),
  });
}

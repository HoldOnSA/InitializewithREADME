/**
 * Client-side instruction building for the StackApp program.
 *
 * Instructions are assembled here and signed by the user's wallet adapter. The
 * app never sees a private key and never sends one anywhere - see README,
 * "Out of scope".
 *
 * Anchor's wire format is simple enough to build directly:
 *   data = sha256("global:<snake_case_name>")[0..8] ++ borsh(args)
 * which avoids shipping a generated IDL that can silently drift from the
 * deployed program. The layouts here mirror `programs/stackapp/src` exactly,
 * and `indexer/stackapp_indexer/layouts.py` mirrors the same structs for
 * decoding.
 */

import { sha256 } from "@noble/hashes/sha256";
import {
  PublicKey,
  SystemProgram,
  TransactionInstruction,
} from "@solana/web3.js";

export const PROGRAM_ID = new PublicKey(
  process.env.NEXT_PUBLIC_PROGRAM_ID ?? "Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS"
);

const SEED_CONFIG = new TextEncoder().encode("config");
const SEED_POOL = new TextEncoder().encode("pool");
const SEED_POSITION = new TextEncoder().encode("position");
const SEED_REPUTATION = new TextEncoder().encode("reputation");
const SEED_CURVE_VAULT = new TextEncoder().encode("curve_vault");

export const BPS_DENOMINATOR = 10_000;
export const MIN_CLAIM_DELAY_SLOTS = 4;
export const MAX_TAX_BPS = 9_000;
export const MAX_TAX_CURVE_POINTS = 8;

export type TaxPoint = { secondsHeld: number | bigint; taxBps: number };

/* -------------------------------------------------------------------------- */
/* borsh                                                                       */
/* -------------------------------------------------------------------------- */

class Encoder {
  private parts: Uint8Array[] = [];

  u8(value: number): this {
    this.parts.push(Uint8Array.of(value & 0xff));
    return this;
  }

  u16(value: number): this {
    const buf = new Uint8Array(2);
    new DataView(buf.buffer).setUint16(0, value, true);
    this.parts.push(buf);
    return this;
  }

  u32(value: number): this {
    const buf = new Uint8Array(4);
    new DataView(buf.buffer).setUint32(0, value, true);
    this.parts.push(buf);
    return this;
  }

  u64(value: bigint | number): this {
    const buf = new Uint8Array(8);
    new DataView(buf.buffer).setBigUint64(0, BigInt(value), true);
    this.parts.push(buf);
    return this;
  }

  i64(value: bigint | number): this {
    const buf = new Uint8Array(8);
    new DataView(buf.buffer).setBigInt64(0, BigInt(value), true);
    this.parts.push(buf);
    return this;
  }

  bytes(value: Uint8Array): this {
    this.parts.push(value);
    return this;
  }

  finish(): Buffer {
    const total = this.parts.reduce((sum, p) => sum + p.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const part of this.parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return Buffer.from(out);
  }
}

/** Anchor's 8-byte instruction discriminator. */
export function discriminator(name: string): Uint8Array {
  return sha256(new TextEncoder().encode(`global:${name}`)).slice(0, 8);
}

/* -------------------------------------------------------------------------- */
/* PDAs                                                                        */
/* -------------------------------------------------------------------------- */

export function tokenConfigPda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_CONFIG, mint.toBytes()], PROGRAM_ID);
}

export function loyaltyPoolPda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_POOL, mint.toBytes()], PROGRAM_ID);
}

export function curveVaultPda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_CURVE_VAULT, mint.toBytes()], PROGRAM_ID);
}

export function positionPda(mint: PublicKey, owner: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync(
    [SEED_POSITION, mint.toBytes(), owner.toBytes()],
    PROGRAM_ID
  );
}

export function reputationPda(owner: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_REPUTATION, owner.toBytes()], PROGRAM_ID);
}

/* -------------------------------------------------------------------------- */
/* instructions                                                                */
/* -------------------------------------------------------------------------- */

function ix(keys: { pubkey: PublicKey; isSigner: boolean; isWritable: boolean }[], data: Buffer) {
  return new TransactionInstruction({ programId: PROGRAM_ID, keys, data });
}

const signer = (pubkey: PublicKey, isWritable = true) => ({ pubkey, isSigner: true, isWritable });
const ro = (pubkey: PublicKey) => ({ pubkey, isSigner: false, isWritable: false });
const rw = (pubkey: PublicKey) => ({ pubkey, isSigner: false, isWritable: true });

export function initializeLaunchIx(params: {
  creator: PublicKey;
  mint: PublicKey;
  taxCurve: TaxPoint[];
  vestDurationSeconds: number | bigint;
  virtualSolReserves?: bigint;
  virtualTokenReserves?: bigint;
  decimals?: number;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [vault] = curveVaultPda(params.mint);

  const encoder = new Encoder()
    .bytes(discriminator("initialize_launch"))
    .u32(params.taxCurve.length);
  for (const point of params.taxCurve) {
    encoder.i64(point.secondsHeld).u16(point.taxBps);
  }
  encoder
    .i64(params.vestDurationSeconds)
    .u64(params.virtualSolReserves ?? 0n)
    .u64(params.virtualTokenReserves ?? 0n)
    .u8(params.decimals ?? 6);

  return ix(
    [
      signer(params.creator),
      ro(params.mint),
      rw(config),
      rw(pool),
      rw(vault),
      ro(SystemProgram.programId),
    ],
    encoder.finish()
  );
}

export function buyIx(params: {
  buyer: PublicKey;
  mint: PublicKey;
  amount: bigint;
  maxCostLamports?: bigint;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.buyer);
  const [vault] = curveVaultPda(params.mint);

  const data = new Encoder()
    .bytes(discriminator("buy"))
    .u64(params.amount)
    .u64(params.maxCostLamports ?? 0n)
    .finish();

  return ix(
    [
      signer(params.buyer),
      ro(params.mint),
      rw(config),
      rw(pool),
      rw(position),
      rw(vault),
      ro(SystemProgram.programId),
    ],
    data
  );
}

export function claimVestedIx(params: {
  owner: PublicKey;
  mint: PublicKey;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.owner);

  return ix(
    [signer(params.owner, false), ro(params.mint), ro(config), rw(pool), rw(position)],
    new Encoder().bytes(discriminator("claim_vested")).finish()
  );
}

export function sellIx(params: {
  seller: PublicKey;
  mint: PublicKey;
  amount: bigint;
  minProceedsLamports?: bigint;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.seller);
  const [vault] = curveVaultPda(params.mint);

  const data = new Encoder()
    .bytes(discriminator("sell"))
    .u64(params.amount)
    .u64(params.minProceedsLamports ?? 0n)
    .finish();

  return ix(
    [
      signer(params.seller),
      ro(params.mint),
      rw(config),
      rw(pool),
      rw(position),
      rw(vault),
      ro(SystemProgram.programId),
    ],
    data
  );
}

export function transferPositionIx(params: {
  sender: PublicKey;
  recipient: PublicKey;
  mint: PublicKey;
  amount: bigint;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [from] = positionPda(params.mint, params.sender);
  const [to] = positionPda(params.mint, params.recipient);

  const data = new Encoder()
    .bytes(discriminator("transfer_position"))
    .u64(params.amount)
    .finish();

  return ix(
    [
      signer(params.sender),
      ro(params.mint),
      ro(params.recipient),
      rw(config),
      rw(pool),
      rw(from),
      rw(to),
      ro(SystemProgram.programId),
    ],
    data
  );
}

export function donateToPoolIx(params: {
  owner: PublicKey;
  mint: PublicKey;
  amount: bigint;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.owner);

  const data = new Encoder()
    .bytes(discriminator("donate_to_pool"))
    .u64(params.amount)
    .finish();

  return ix(
    [signer(params.owner, false), ro(params.mint), rw(config), rw(pool), rw(position)],
    data
  );
}

export function claimPoolShareIx(params: {
  owner: PublicKey;
  mint: PublicKey;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.owner);

  return ix(
    [signer(params.owner, false), ro(params.mint), ro(config), rw(pool), rw(position)],
    new Encoder().bytes(discriminator("claim_pool_share")).finish()
  );
}

export function updateReputationIx(params: {
  owner: PublicKey;
  mint: PublicKey;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.owner);
  const [reputation] = reputationPda(params.owner);

  return ix(
    [
      signer(params.owner),
      ro(params.mint),
      ro(config),
      rw(pool),
      rw(position),
      rw(reputation),
      ro(SystemProgram.programId),
    ],
    new Encoder().bytes(discriminator("update_reputation")).finish()
  );
}

export function syncWeightIx(params: {
  cranker: PublicKey;
  owner: PublicKey;
  mint: PublicKey;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.owner);

  return ix(
    [
      signer(params.cranker, false),
      ro(params.mint),
      ro(params.owner),
      ro(config),
      rw(pool),
      rw(position),
    ],
    new Encoder().bytes(discriminator("sync_weight")).finish()
  );
}

export function compactLotsIx(params: {
  owner: PublicKey;
  mint: PublicKey;
}): TransactionInstruction {
  const [config] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [position] = positionPda(params.mint, params.owner);

  return ix(
    [signer(params.owner, false), ro(params.mint), ro(config), rw(pool), rw(position)],
    new Encoder().bytes(discriminator("compact_lots")).finish()
  );
}

/* -------------------------------------------------------------------------- */
/* tax curve presets, mirrored from sim/stackapp_sim/__init__.py               */
/* -------------------------------------------------------------------------- */

export const TAX_CURVE_PRESETS: Record<
  string,
  { label: string; blurb: string; points: TaxPoint[] }
> = {
  diamond: {
    label: "Diamond hands",
    blurb: "30% to start, free after a week. The default shape.",
    points: [
      { secondsHeld: 0, taxBps: 3_000 },
      { secondsHeld: 3_600, taxBps: 2_000 },
      { secondsHeld: 86_400, taxBps: 1_000 },
      { secondsHeld: 604_800, taxBps: 0 },
    ],
  },
  gentle: {
    label: "Gentle",
    blurb: "10% to start. Discourages flipping without punishing it.",
    points: [
      { secondsHeld: 0, taxBps: 1_000 },
      { secondsHeld: 3_600, taxBps: 500 },
      { secondsHeld: 86_400, taxBps: 200 },
      { secondsHeld: 604_800, taxBps: 0 },
    ],
  },
  brutal: {
    label: "Brutal",
    blurb: "90% to start and never reaches zero. For long-horizon launches.",
    points: [
      { secondsHeld: 0, taxBps: 9_000 },
      { secondsHeld: 3_600, taxBps: 6_000 },
      { secondsHeld: 86_400, taxBps: 3_000 },
      { secondsHeld: 2_592_000, taxBps: 500 },
    ],
  },
  flat: {
    label: "Flat",
    blurb: "A constant 5%. No tenure incentive at all - useful as a control.",
    points: [{ secondsHeld: 0, taxBps: 500 }],
  },
};

/**
 * The same validation the program applies, so the form can reject a bad curve
 * before it costs a transaction. Mirrors `math::validate_tax_curve`.
 */
export function validateTaxCurve(points: TaxPoint[]): string | null {
  if (points.length === 0) return "A curve needs at least one point.";
  if (points.length > MAX_TAX_CURVE_POINTS)
    return `At most ${MAX_TAX_CURVE_POINTS} points.`;
  if (Number(points[0].secondsHeld) !== 0)
    return "The first point must be at 0 seconds held.";
  for (let i = 0; i < points.length; i += 1) {
    if (points[i].taxBps > MAX_TAX_BPS)
      return `Tax cannot exceed ${MAX_TAX_BPS / 100}%.`;
    if (points[i].taxBps < 0) return "Tax cannot be negative.";
    if (i > 0) {
      if (Number(points[i].secondsHeld) <= Number(points[i - 1].secondsHeld))
        return "Points must increase in seconds held.";
      if (points[i].taxBps > points[i - 1].taxBps)
        return "Tax must never rise with time held - that would reward selling sooner.";
    }
  }
  return null;
}

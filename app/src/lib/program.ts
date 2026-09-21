/**
 * Client-side instruction building for the StackApp program.
 *
 * Instructions are assembled here and signed by the user's wallet adapter. The
 * app never sees a private key and never sends one anywhere - see README,
 * "Signing". This includes `registerMint` and `writeRegistration`, which are
 * authority-gated on chain (`GlobalConfig.authority`) but built and signed
 * exactly like everything else: whichever wallet the operator connects. If
 * that isn't the real authority, the transaction just fails on chain.
 *
 * Anchor's wire format is simple enough to build directly:
 *   data = sha256("global:<snake_case_name>")[0..8] ++ borsh(args)
 * which avoids shipping a generated IDL that can silently drift from the
 * deployed program. The layouts here mirror `programs/stackapp/src` exactly,
 * and `indexer/stackapp_indexer/txbuild.py` builds the same instructions
 * server-side for the Python UI.
 */

import { sha256 } from "@noble/hashes/sha256";
import {
  PublicKey,
  SystemProgram,
  TransactionInstruction,
} from "@solana/web3.js";

export const PROGRAM_ID = new PublicKey(
  process.env.NEXT_PUBLIC_PROGRAM_ID ?? "BBAbsh9UHVt7xiqeuVo23R4MPbNzXXNp2gsLwu6jAa1M"
);

const SEED_GLOBAL_CONFIG = new TextEncoder().encode("global_config");
const SEED_CONFIG = new TextEncoder().encode("config");
const SEED_POOL = new TextEncoder().encode("pool");
const SEED_DEPOSIT_VAULT = new TextEncoder().encode("deposit");
const SEED_REGISTRATION = new TextEncoder().encode("registration");

export const BPS_DENOMINATOR = 10_000;
export const MIN_CLAIM_DELAY_SLOTS = 4;
export const TENURE_TIER_SECONDS = [60, 600, 1_800] as const; // 1 min, 10 min, 30 min
export const TENURE_TIER_MULTIPLIER_BPS = [10_000, 12_500, 15_000, 20_000] as const;
export const REGISTRATION_MARKER_LAMPORTS = 2_500_000n; // 0.0025 SOL

/** Legacy SPL Token and Token-2022 - real pump.fun mints split roughly 14:1
 * Token-2022 vs legacy (verified against sampled mainnet tokens), so the
 * owning program is always read from the mint account, never assumed. */
export const TOKEN_PROGRAM_ID = new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
export const TOKEN_2022_PROGRAM_ID = new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
export const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey(
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
);

/* -------------------------------------------------------------------------- */
/* borsh                                                                       */
/* -------------------------------------------------------------------------- */

class Encoder {
  private parts: Uint8Array[] = [];

  bytes(value: Uint8Array): this {
    this.parts.push(value);
    return this;
  }

  u8(value: number): this {
    this.parts.push(Uint8Array.of(value & 0xff));
    return this;
  }

  u64(value: bigint | number): this {
    const buf = new Uint8Array(8);
    new DataView(buf.buffer).setBigUint64(0, BigInt(value), true);
    this.parts.push(buf);
    return this;
  }

  pubkey(value: PublicKey): this {
    this.parts.push(value.toBytes());
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

export function globalConfigPda(): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_GLOBAL_CONFIG], PROGRAM_ID);
}

export function tokenConfigPda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_CONFIG, mint.toBytes()], PROGRAM_ID);
}

export function loyaltyPoolPda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_POOL, mint.toBytes()], PROGRAM_ID);
}

export function depositVaultPda(mint: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([SEED_DEPOSIT_VAULT, mint.toBytes()], PROGRAM_ID);
}

export function registrationPda(mint: PublicKey, owner: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync(
    [SEED_REGISTRATION, mint.toBytes(), owner.toBytes()],
    PROGRAM_ID
  );
}

/** `owner`'s canonical ATA for `mint`, under whichever token program actually
 * owns the mint (`tokenProgram` - read it off the mint account, never
 * assumed; see `resolveTokenProgram` in `api.ts`-adjacent call sites). */
export function deriveAta(owner: PublicKey, mint: PublicKey, tokenProgram: PublicKey): PublicKey {
  return PublicKey.findProgramAddressSync(
    [owner.toBytes(), tokenProgram.toBytes(), mint.toBytes()],
    ASSOCIATED_TOKEN_PROGRAM_ID
  )[0];
}

/* -------------------------------------------------------------------------- */
/* tenure math - mirrors math/tenure.rs                                       */
/* -------------------------------------------------------------------------- */

export function tenureMultiplierBps(secondsHeld: number): number {
  const held = Math.max(secondsHeld, 0);
  let mult: number = TENURE_TIER_MULTIPLIER_BPS[0];
  TENURE_TIER_SECONDS.forEach((threshold, i) => {
    if (held >= threshold) mult = TENURE_TIER_MULTIPLIER_BPS[i + 1];
  });
  return mult;
}

/* -------------------------------------------------------------------------- */
/* instructions                                                               */
/* -------------------------------------------------------------------------- */

function ix(keys: { pubkey: PublicKey; isSigner: boolean; isWritable: boolean }[], data: Buffer) {
  return new TransactionInstruction({ programId: PROGRAM_ID, keys, data });
}

const signer = (pubkey: PublicKey, isWritable = true) => ({ pubkey, isSigner: true, isWritable });
const ro = (pubkey: PublicKey) => ({ pubkey, isSigner: false, isWritable: false });
const rw = (pubkey: PublicKey) => ({ pubkey, isSigner: false, isWritable: true });

export function initializeConfigIx(params: {
  payer: PublicKey;
  authority: PublicKey;
}): TransactionInstruction {
  const [globalConfig] = globalConfigPda();
  return ix(
    [signer(params.payer), rw(globalConfig), ro(SystemProgram.programId)],
    new Encoder().bytes(discriminator("initialize_config")).pubkey(params.authority).finish()
  );
}

export function updateAuthorityIx(params: {
  authority: PublicKey;
  newAuthority: PublicKey;
}): TransactionInstruction {
  const [globalConfig] = globalConfigPda();
  return ix(
    [signer(params.authority, false), rw(globalConfig)],
    new Encoder().bytes(discriminator("update_authority")).pubkey(params.newAuthority).finish()
  );
}

export function registerMintIx(params: {
  authority: PublicKey;
  mint: PublicKey;
  creator: PublicKey;
}): TransactionInstruction {
  const [globalConfig] = globalConfigPda();
  const [tokenConfig] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [vault] = depositVaultPda(params.mint);

  return ix(
    [
      signer(params.authority),
      ro(globalConfig),
      ro(params.mint),
      rw(tokenConfig),
      rw(pool),
      rw(vault),
      ro(SystemProgram.programId),
    ],
    new Encoder().bytes(discriminator("register_mint")).pubkey(params.creator).finish()
  );
}

export function writeRegistrationIx(params: {
  authority: PublicKey;
  owner: PublicKey;
  mint: PublicKey;
}): TransactionInstruction {
  const [globalConfig] = globalConfigPda();
  const [tokenConfig] = tokenConfigPda(params.mint);
  const [vault] = depositVaultPda(params.mint);
  const [registration] = registrationPda(params.mint, params.owner);

  return ix(
    [
      signer(params.authority, false),
      ro(globalConfig),
      ro(params.owner),
      ro(params.mint),
      ro(tokenConfig),
      rw(vault),
      rw(registration),
      ro(SystemProgram.programId),
    ],
    new Encoder().bytes(discriminator("write_registration")).finish()
  );
}

export function syncIx(params: {
  cranker: PublicKey;
  owner: PublicKey;
  mint: PublicKey;
  tokenProgram: PublicKey;
}): TransactionInstruction {
  const [tokenConfig] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [registration] = registrationPda(params.mint, params.owner);
  const holderTokenAccount = deriveAta(params.owner, params.mint, params.tokenProgram);

  return ix(
    [
      signer(params.cranker, false),
      ro(params.mint),
      ro(params.owner),
      ro(tokenConfig),
      rw(pool),
      rw(registration),
      ro(holderTokenAccount),
    ],
    new Encoder().bytes(discriminator("sync")).finish()
  );
}

export function claimIx(params: {
  owner: PublicKey;
  mint: PublicKey;
  tokenProgram: PublicKey;
}): TransactionInstruction {
  const [tokenConfig] = tokenConfigPda(params.mint);
  const [pool] = loyaltyPoolPda(params.mint);
  const [registration] = registrationPda(params.mint, params.owner);
  const [vault] = depositVaultPda(params.mint);
  const holderTokenAccount = deriveAta(params.owner, params.mint, params.tokenProgram);

  return ix(
    [
      signer(params.owner),
      ro(params.mint),
      ro(tokenConfig),
      rw(pool),
      rw(registration),
      rw(vault),
      ro(holderTokenAccount),
    ],
    new Encoder().bytes(discriminator("claim")).finish()
  );
}

export function donateIx(params: {
  donor: PublicKey;
  mint: PublicKey;
  amount: bigint;
}): TransactionInstruction {
  const [pool] = loyaltyPoolPda(params.mint);
  const [vault] = depositVaultPda(params.mint);

  return ix(
    [signer(params.donor), ro(params.mint), rw(pool), rw(vault), ro(SystemProgram.programId)],
    new Encoder().bytes(discriminator("donate")).u64(params.amount).finish()
  );
}

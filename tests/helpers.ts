import * as anchor from "@coral-xyz/anchor";
import { Program } from "@coral-xyz/anchor";
import { Keypair, LAMPORTS_PER_SOL, PublicKey } from "@solana/web3.js";
import { Stackapp } from "../target/types/stackapp";

export const BPS = 10_000;
export const MIN_CLAIM_DELAY_SLOTS = 4;

export type TaxPoint = { secondsHeld: anchor.BN; taxBps: number };

export const point = (secondsHeld: number, taxBps: number): TaxPoint => ({
  secondsHeld: new anchor.BN(secondsHeld),
  taxBps,
});

/**
 * Curves used by the tests.
 *
 * The boundaries are seconds rather than days: a local validator has no clock
 * warp, so anything time-dependent has to be exercised by actually waiting.
 */
export const CURVES = {
  /** 30% immediately, 10% after 2s, 0% after 4s. */
  fast: [point(0, 3_000), point(2, 1_000), point(4, 0)],
  /** A constant 20%, for isolating everything except the decay. */
  flat: [point(0, 2_000)],
};

/**
 * Reputation score is `capital_lamports * window_seconds / REP_SCORE_DIVISOR`
 * (1 SOL held 1 day == 1000 points), a calibration meant for real holding
 * periods measured in days. Against the *default* bonding curve, a
 * test-sized buy costs only a fraction of a lamport-day of capital, so held
 * for the few seconds a local validator can actually wait, its score rounds
 * down to exactly zero.
 *
 * A tiny, shallow curve makes a fundable amount of real SOL buy nearly the
 * *entire* virtual supply, which is what pushes `cost_basis_lamports` high
 * enough (~247 SOL, for a ~99% buy) to clear that threshold within seconds
 * instead of days. Pair with `REPUTATION_TEST_BUY_AMOUNT` and fund the buyer
 * well above the resulting cost.
 */
export const REPUTATION_CURVE_RESERVES = {
  virtualSolReserves: 2_500_000_000, // 2.5 SOL
  virtualTokenReserves: 1_000_000,
};
export const REPUTATION_TEST_BUY_AMOUNT = 990_000; // 99% of the virtual supply above

export const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Wait until the validator's wall clock has advanced past `seconds`. */
export async function waitSeconds(
  provider: anchor.AnchorProvider,
  seconds: number
): Promise<void> {
  const start = await chainTime(provider);
  // Poll rather than sleeping blind: the validator clock can drift from ours.
  for (let i = 0; i < 200; i += 1) {
    await sleep(250);
    if ((await chainTime(provider)) >= start + seconds) return;
  }
  throw new Error(`validator clock did not advance ${seconds}s`);
}

export async function chainTime(provider: anchor.AnchorProvider): Promise<number> {
  const slot = await provider.connection.getSlot("confirmed");
  const time = await provider.connection.getBlockTime(slot);
  if (time === null) throw new Error("no block time");
  return time;
}

export async function waitSlots(
  provider: anchor.AnchorProvider,
  slots: number
): Promise<void> {
  const start = await provider.connection.getSlot("confirmed");
  for (let i = 0; i < 400; i += 1) {
    await sleep(100);
    if ((await provider.connection.getSlot("confirmed")) >= start + slots) return;
  }
  throw new Error(`validator did not advance ${slots} slots`);
}

export async function fundedWallet(
  provider: anchor.AnchorProvider,
  sol = 20
): Promise<Keypair> {
  const wallet = Keypair.generate();
  const signature = await provider.connection.requestAirdrop(
    wallet.publicKey,
    sol * LAMPORTS_PER_SOL
  );
  const blockhash = await provider.connection.getLatestBlockhash("confirmed");
  await provider.connection.confirmTransaction(
    { signature, ...blockhash },
    "confirmed"
  );
  return wallet;
}

/* -------------------------------------------------------------------------- */
/* PDAs                                                                        */
/* -------------------------------------------------------------------------- */

export const pdas = (programId: PublicKey) => ({
  config: (mint: PublicKey) =>
    PublicKey.findProgramAddressSync(
      [Buffer.from("config"), mint.toBuffer()],
      programId
    )[0],
  pool: (mint: PublicKey) =>
    PublicKey.findProgramAddressSync([Buffer.from("pool"), mint.toBuffer()], programId)[0],
  curveVault: (mint: PublicKey) =>
    PublicKey.findProgramAddressSync(
      [Buffer.from("curve_vault"), mint.toBuffer()],
      programId
    )[0],
  position: (mint: PublicKey, owner: PublicKey) =>
    PublicKey.findProgramAddressSync(
      [Buffer.from("position"), mint.toBuffer(), owner.toBuffer()],
      programId
    )[0],
  reputation: (owner: PublicKey) =>
    PublicKey.findProgramAddressSync(
      [Buffer.from("reputation"), owner.toBuffer()],
      programId
    )[0],
});

/* -------------------------------------------------------------------------- */
/* A launch, plus the calls against it                                         */
/* -------------------------------------------------------------------------- */

export class Launch {
  readonly mint: PublicKey;
  readonly p: ReturnType<typeof pdas>;

  private constructor(
    readonly program: Program<Stackapp>,
    readonly provider: anchor.AnchorProvider,
    mint: PublicKey
  ) {
    this.mint = mint;
    this.p = pdas(program.programId);
  }

  static async create(
    program: Program<Stackapp>,
    provider: anchor.AnchorProvider,
    creator: Keypair,
    opts: {
      taxCurve?: TaxPoint[];
      vestSeconds?: number;
      virtualSolReserves?: anchor.BN | number;
      virtualTokenReserves?: anchor.BN | number;
    } = {}
  ): Promise<Launch> {
    const mint = Keypair.generate();
    const p = pdas(program.programId);

    await program.methods
      .initializeLaunch(
        opts.taxCurve ?? CURVES.fast,
        new anchor.BN(opts.vestSeconds ?? 0),
        new anchor.BN(opts.virtualSolReserves ?? 0), // 0 == prototype default
        new anchor.BN(opts.virtualTokenReserves ?? 0), // 0 == prototype default
        6
      )
      .accounts({
        creator: creator.publicKey,
        mint: mint.publicKey,
        tokenConfig: p.config(mint.publicKey),
        loyaltyPool: p.pool(mint.publicKey),
        curveVault: p.curveVault(mint.publicKey),
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([creator])
      .rpc();

    return new Launch(program, provider, mint.publicKey);
  }

  buy(wallet: Keypair, amount: anchor.BN | number) {
    return this.program.methods
      .buy(new anchor.BN(amount), new anchor.BN(0))
      .accounts({
        buyer: wallet.publicKey,
        mint: this.mint,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, wallet.publicKey),
        curveVault: this.p.curveVault(this.mint),
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([wallet])
      .rpc();
  }

  claimVested(wallet: Keypair) {
    return this.program.methods
      .claimVested()
      .accounts({
        owner: wallet.publicKey,
        mint: this.mint,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, wallet.publicKey),
      })
      .signers([wallet])
      .rpc();
  }

  sell(wallet: Keypair, amount: anchor.BN | number) {
    return this.program.methods
      .sell(new anchor.BN(amount), new anchor.BN(0))
      .accounts({
        seller: wallet.publicKey,
        mint: this.mint,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, wallet.publicKey),
        curveVault: this.p.curveVault(this.mint),
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([wallet])
      .rpc();
  }

  transfer(sender: Keypair, recipient: PublicKey, amount: anchor.BN | number) {
    return this.program.methods
      .transferPosition(new anchor.BN(amount))
      .accounts({
        sender: sender.publicKey,
        mint: this.mint,
        recipient,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        fromPosition: this.p.position(this.mint, sender.publicKey),
        toPosition: this.p.position(this.mint, recipient),
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([sender])
      .rpc();
  }

  donate(wallet: Keypair, amount: anchor.BN | number) {
    return this.program.methods
      .donateToPool(new anchor.BN(amount))
      .accounts({
        owner: wallet.publicKey,
        mint: this.mint,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, wallet.publicKey),
      })
      .signers([wallet])
      .rpc();
  }

  claimPoolShare(wallet: Keypair) {
    return this.program.methods
      .claimPoolShare()
      .accounts({
        owner: wallet.publicKey,
        mint: this.mint,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, wallet.publicKey),
      })
      .signers([wallet])
      .rpc();
  }

  updateReputation(wallet: Keypair) {
    return this.program.methods
      .updateReputation()
      .accounts({
        owner: wallet.publicKey,
        mint: this.mint,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, wallet.publicKey),
        reputation: this.p.reputation(wallet.publicKey),
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([wallet])
      .rpc();
  }

  syncWeight(cranker: Keypair, owner: PublicKey) {
    return this.program.methods
      .syncWeight()
      .accounts({
        cranker: cranker.publicKey,
        mint: this.mint,
        owner,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, owner),
      })
      .signers([cranker])
      .rpc();
  }

  compactLots(wallet: Keypair) {
    return this.program.methods
      .compactLots()
      .accounts({
        owner: wallet.publicKey,
        mint: this.mint,
        tokenConfig: this.p.config(this.mint),
        loyaltyPool: this.p.pool(this.mint),
        position: this.p.position(this.mint, wallet.publicKey),
      })
      .signers([wallet])
      .rpc();
  }

  /* -- reads -- */

  config() {
    return this.program.account.tokenConfig.fetch(this.p.config(this.mint));
  }

  pool() {
    return this.program.account.loyaltyPool.fetch(this.p.pool(this.mint));
  }

  position(owner: PublicKey) {
    return this.program.account.position.fetch(this.p.position(this.mint, owner));
  }

  positionOrNull(owner: PublicKey) {
    return this.program.account.position
      .fetch(this.p.position(this.mint, owner))
      .catch(() => null);
  }

  reputation(owner: PublicKey) {
    return this.program.account.reputation.fetch(this.p.reputation(owner));
  }

  vaultLamports() {
    return this.provider.connection.getBalance(this.p.curveVault(this.mint), "confirmed");
  }
}

/** Assert that a call fails, and that the message mentions `needle`. */
export async function expectFailure(
  promise: Promise<unknown>,
  needle: string
): Promise<void> {
  try {
    await promise;
  } catch (error) {
    const message = String((error as Error)?.message ?? error);
    if (!message.includes(needle)) {
      throw new Error(`expected failure mentioning "${needle}", got: ${message}`);
    }
    return;
  }
  throw new Error(`expected a failure mentioning "${needle}", but the call succeeded`);
}

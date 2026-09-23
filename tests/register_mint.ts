/**
 * On-chain integration test, against a local validator: `anchor test`.
 *
 * Deliberately self-contained rather than built on `helpers.ts`/`stackapp.ts` -
 * both of those still target the pre-pivot bonding-curve design (`buy`,
 * `sell`, `Position`, `Reputation`, ...) and don't compile against the
 * current program's IDL at all. Rewriting the whole suite for the current
 * (pump.fun loyalty layer) design is a separate, larger task; this file
 * exists to prove one specific claim, made in review, that deserved real
 * on-chain evidence rather than confidence in Anchor's general `init`
 * behaviour: a `DepositVault` PDA that already holds real lamports *before*
 * `register_mint` ever runs must survive `init` with that balance intact,
 * and the vault-reconciliation math must then correctly treat it as a real,
 * sweepable surplus.
 */

import * as anchor from "@coral-xyz/anchor";
import { Program } from "@coral-xyz/anchor";
import { Keypair, LAMPORTS_PER_SOL, PublicKey } from "@solana/web3.js";
import { assert } from "chai";

import { Stackapp } from "../target/types/stackapp";

const SEED_GLOBAL_CONFIG = Buffer.from("global_config");
const SEED_CONFIG = Buffer.from("config");
const SEED_POOL = Buffer.from("pool");
const SEED_DEPOSIT_VAULT = Buffer.from("deposit");

function pdas(programId: PublicKey) {
  return {
    globalConfig: () =>
      PublicKey.findProgramAddressSync([SEED_GLOBAL_CONFIG], programId)[0],
    config: (mint: PublicKey) =>
      PublicKey.findProgramAddressSync([SEED_CONFIG, mint.toBuffer()], programId)[0],
    pool: (mint: PublicKey) =>
      PublicKey.findProgramAddressSync([SEED_POOL, mint.toBuffer()], programId)[0],
    depositVault: (mint: PublicKey) =>
      PublicKey.findProgramAddressSync(
        [SEED_DEPOSIT_VAULT, mint.toBuffer()],
        programId
      )[0],
  };
}

async function fundedWallet(
  provider: anchor.AnchorProvider,
  sol = 20
): Promise<Keypair> {
  const wallet = Keypair.generate();
  const signature = await provider.connection.requestAirdrop(
    wallet.publicKey,
    sol * LAMPORTS_PER_SOL
  );
  const blockhash = await provider.connection.getLatestBlockhash("confirmed");
  await provider.connection.confirmTransaction({ signature, ...blockhash }, "confirmed");
  return wallet;
}

describe("register_mint against a pre-funded DepositVault", () => {
  const env = anchor.AnchorProvider.env();
  const provider = new anchor.AnchorProvider(
    new anchor.web3.Connection(env.connection.rpcEndpoint, "confirmed"),
    env.wallet,
    { commitment: "confirmed", preflightCommitment: "confirmed" }
  );
  anchor.setProvider(provider);
  const program = anchor.workspace.Stackapp as Program<Stackapp>;
  const p = pdas(program.programId);

  let authority: Keypair;

  before(async () => {
    authority = await fundedWallet(provider);
    // Fresh validator, fresh ledger per `anchor test` run - `GlobalConfig`
    // doesn't exist yet. `init` means this can only ever succeed once.
    await program.methods
      .initializeConfig(authority.publicKey)
      .accounts({
        payer: authority.publicKey,
        globalConfig: p.globalConfig(),
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([authority])
      .rpc();
  });

  it("keeps a pre-funded balance intact through init, as a real, sweepable surplus", async () => {
    const mint = Keypair.generate().publicKey;
    const depositVault = p.depositVault(mint);
    // Deliberately well above DepositVault's own rent-exemption (~1.29M
    // lamports for its 57-byte size) - a pre-fund amount *below* that
    // threshold can't actually distinguish "the surplus survived" from
    // "the account was simply topped up to rent-exemption from scratch and
    // the pre-funded amount was absorbed into that same requirement",
    // which is exactly the mistake the first version of this test made.
    const PRE_FUND_LAMPORTS = 5_000_000;

    // Fund the PDA *before* `register_mint` has ever run. Its address is a
    // deterministic PDA, computable off-chain from `mint` alone - nothing
    // stops real SOL (e.g. an early pump.fun payout landing before the
    // creator has even been onboarded) from arriving here first.
    const preFundTx = new anchor.web3.Transaction().add(
      anchor.web3.SystemProgram.transfer({
        fromPubkey: authority.publicKey,
        toPubkey: depositVault,
        lamports: PRE_FUND_LAMPORTS,
      })
    );
    await provider.sendAndConfirm(preFundTx, [authority], { commitment: "confirmed" });

    const balanceBeforeInit = await provider.connection.getBalance(
      depositVault,
      "confirmed"
    );
    assert.equal(
      balanceBeforeInit,
      PRE_FUND_LAMPORTS,
      "the pre-fund transfer must have landed before register_mint runs"
    );

    // The claim under test: `init` on an already-funded PDA must succeed -
    // not fail with "account already in use" - and must not overwrite or
    // truncate the balance already sitting there.
    await program.methods
      .registerMint(authority.publicKey) // `creator` arg is informational only
      .accounts({
        authority: authority.publicKey,
        globalConfig: p.globalConfig(),
        mint,
        tokenConfig: p.config(mint),
        loyaltyPool: p.pool(mint),
        depositVault,
        systemProgram: anchor.web3.SystemProgram.programId,
      })
      .signers([authority])
      .rpc();

    // Derived from the account's real, deployed size - not a hand-copied
    // byte count - so this can't silently drift from `DepositVault`'s
    // actual layout the way a hardcoded constant could.
    const accountInfo = await provider.connection.getAccountInfo(depositVault, "confirmed");
    assert.isNotNull(accountInfo, "register_mint must have actually created the account");
    const rentExemptMinimum = await provider.connection.getMinimumBalanceForRentExemption(
      accountInfo!.data.length
    );

    const balanceAfterInit = await provider.connection.getBalance(depositVault, "confirmed");
    // The account was already above its own rent-exemption before `init`
    // ever ran, so there is no shortfall for `init` to top up - the correct,
    // desired behaviour is that the balance is untouched, not that a fresh
    // rent allocation gets stacked on top of it. (An earlier version of this
    // test asserted `rentExemptMinimum + PRE_FUND_LAMPORTS` here, which
    // assumes `init` always adds a full rent allocation regardless of the
    // existing balance - that assumption was wrong, and the real, deployed
    // account is proof: the first run of this test against the corrected
    // pre-fund amount below failed against that formula, which is what
    // caught the mistake.)
    assert.equal(
      balanceAfterInit,
      PRE_FUND_LAMPORTS,
      "a balance already above rent-exemption must survive `init` completely unchanged - not overwritten, and not topped up further"
    );

    const vault = await program.account.depositVault.fetch(depositVault);
    assert.equal(vault.totalMarkerDeposits.toNumber(), 0, "a freshly registered vault has no marker history yet");
    assert.equal(vault.totalRentSpent.toNumber(), 0, "a freshly registered vault has spent no rent yet");

    // The claim's second half: with the vault's own bookkeeping freshly
    // zeroed, `vault_expected_balance` is just the rent floor - so only the
    // amount *above* that floor is a genuine, sweepable surplus (the floor
    // itself is never spendable/sweepable, by design - see
    // `logic::vault_expected_balance`). Prove it the strongest way
    // available: actually call `reconcile` and confirm it sweeps exactly
    // that amount into the pool - not just that the math would say so.
    const expectedSurplus = PRE_FUND_LAMPORTS - rentExemptMinimum;
    assert.isAbove(expectedSurplus, 0, "test setup must pre-fund above rent-exemption for this to be a meaningful surplus check");

    await program.methods
      .reconcile()
      .accounts({
        cranker: authority.publicKey,
        mint,
        loyaltyPool: p.pool(mint),
        depositVault,
      })
      .signers([authority])
      .rpc();

    const pool = await program.account.loyaltyPool.fetch(p.pool(mint));
    assert.equal(
      pool.totalCollected.toNumber(),
      expectedSurplus,
      "reconcile() must sweep exactly the amount above the rent floor into the pool as real fee revenue - not the vault's whole balance, and not zero"
    );

    const vaultAfterReconcile = await provider.connection.getBalance(depositVault, "confirmed");
    assert.equal(
      vaultAfterReconcile,
      PRE_FUND_LAMPORTS,
      "reconcile() only relabels the balance as accounted-for revenue - it never moves any lamports itself"
    );
  });
});

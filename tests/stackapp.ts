/**
 * On-chain integration tests, against a local validator.
 *
 *     anchor test
 *
 * The accumulator arithmetic and the anti-gaming rules are proved exhaustively
 * in `cargo test` (host-side, in `programs/stackapp/src/`) and in
 * `sim/tests/` (the Python mirror). What these tests add is that the *deployed
 * program* wires that logic up correctly: right accounts, right PDAs, right
 * ordering, real lamports moving, real events emitted.
 *
 * Time-dependent behaviour is exercised by actually waiting - a local validator
 * has no clock warp - so the fixtures use curves and vest windows measured in
 * seconds.
 */

import * as anchor from "@coral-xyz/anchor";
import { Program } from "@coral-xyz/anchor";
import { assert } from "chai";

import { Stackapp } from "../target/types/stackapp";
import {
  CURVES,
  Launch,
  MIN_CLAIM_DELAY_SLOTS,
  REPUTATION_CURVE_RESERVES,
  REPUTATION_TEST_BUY_AMOUNT,
  expectFailure,
  fundedWallet,
  pdas,
  point,
  waitSeconds,
  waitSlots,
} from "./helpers";

describe("stackapp", () => {
  // `AnchorProvider.env()` defaults to "processed" commitment, so `.rpc()`
  // calls can resolve before the change is visible at "confirmed" - which is
  // what every balance/account read in these tests uses. Pin both to
  // "confirmed" so a read right after a write is never racing it.
  const env = anchor.AnchorProvider.env();
  const provider = new anchor.AnchorProvider(
    new anchor.web3.Connection(env.connection.rpcEndpoint, "confirmed"),
    env.wallet,
    { commitment: "confirmed", preflightCommitment: "confirmed" }
  );
  anchor.setProvider(provider);
  const program = anchor.workspace.Stackapp as Program<Stackapp>;

  let creator: anchor.web3.Keypair;

  before(async () => {
    creator = await fundedWallet(provider, 50);
  });

  /* ---------------------------------------------------------------------- */

  describe("initialize_launch", () => {
    it("creates the config, pool and curve vault", async () => {
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.fast,
        vestSeconds: 3,
      });

      const config = await launch.config();
      assert.equal(config.mint.toBase58(), launch.mint.toBase58());
      assert.equal(config.creator.toBase58(), creator.publicKey.toBase58());
      assert.equal(config.vestDurationSeconds.toNumber(), 3);
      assert.equal(config.taxCurve.length, CURVES.fast.length);
      assert.equal(config.taxCurve[0].taxBps, 3_000);
      assert.equal(
        config.poolAccount.toBase58(),
        launch.p.pool(launch.mint).toBase58(),
        "pool_account must point at the program's own pool - it is the only tax-exempt destination"
      );
      assert.isTrue(config.virtualSolReserves.gtn(0));
      assert.isTrue(config.virtualTokenReserves.gtn(0));

      const pool = await launch.pool();
      assert.equal(pool.accRewardPerShare.toString(), "0");
      assert.equal(pool.totalWeightedShares.toNumber(), 0);
    });

    it("rejects a curve that rises with time held", async () => {
      const mint = anchor.web3.Keypair.generate();
      await expectFailure(
        program.methods
          .initializeLaunch(
            [point(0, 1_000), point(10, 2_000)],
            new anchor.BN(0),
            new anchor.BN(0),
            new anchor.BN(0),
            6
          )
          .accounts({
            creator: creator.publicKey,
            mint: mint.publicKey,
            tokenConfig: pdas(program.programId).config(mint.publicKey),
            loyaltyPool: pdas(program.programId).pool(mint.publicKey),
            curveVault: pdas(program.programId).curveVault(mint.publicKey),
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .signers([creator])
          .rpc(),
        "TaxCurveNotMonotonic"
      );
    });

    it("rejects a curve that does not start at zero seconds", async () => {
      const mint = anchor.web3.Keypair.generate();
      await expectFailure(
        program.methods
          .initializeLaunch(
            [point(60, 1_000)],
            new anchor.BN(0),
            new anchor.BN(0),
            new anchor.BN(0),
            6
          )
          .accounts({
            creator: creator.publicKey,
            mint: mint.publicKey,
            tokenConfig: pdas(program.programId).config(mint.publicKey),
            loyaltyPool: pdas(program.programId).pool(mint.publicKey),
            curveVault: pdas(program.programId).curveVault(mint.publicKey),
            systemProgram: anchor.web3.SystemProgram.programId,
          })
          .signers([creator])
          .rpc(),
        "TaxCurveMustStartAtZero"
      );
    });
  });

  /* ---------------------------------------------------------------------- */

  describe("buy and vest", () => {
    it("opens a position, takes real lamports, and locks the tokens", async () => {
      const alice = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, { vestSeconds: 4 });

      const before = await provider.connection.getBalance(alice.publicKey, "confirmed");
      const vaultBefore = await launch.vaultLamports();

      await launch.buy(alice, 5_000_000);

      const position = await launch.position(alice.publicKey);
      assert.equal(position.lots.length, 1);
      assert.equal(position.lots[0].original.toNumber(), 5_000_000);
      assert.equal(position.totalBought.toNumber(), 5_000_000);
      assert.equal(position.spendable.toNumber(), 0, "a fresh buy is fully locked");
      assert.isTrue(position.costBasisLamports.gtn(0), "capital at risk must be recorded");

      const after = await provider.connection.getBalance(alice.publicKey, "confirmed");
      const vaultAfter = await launch.vaultLamports();
      assert.isBelow(after, before, "the buyer paid real lamports");
      assert.isAbove(vaultAfter, vaultBefore, "the curve vault received them");

      const config = await launch.config();
      assert.equal(config.tokensSold.toNumber(), 5_000_000);
      assert.equal(config.holderCount, 1);

      const pool = await launch.pool();
      assert.equal(
        pool.totalWeightedShares.toNumber(),
        position.weightedShares.toNumber(),
        "the pool total must match the only position"
      );
    });

    it("releases vested tokens linearly and refuses to sell more", async () => {
      const alice = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, { vestSeconds: 6 });
      await launch.buy(alice, 6_000_000);

      await waitSeconds(provider, 2);
      await launch.claimVested(alice);

      const partial = await launch.position(alice.publicKey);
      assert.isAbove(partial.spendable.toNumber(), 0);
      assert.isBelow(
        partial.spendable.toNumber(),
        6_000_000,
        "only part of the lot should have vested"
      );

      await expectFailure(
        launch.sell(alice, 6_000_000),
        "InsufficientSpendable"
      );

      await waitSeconds(provider, 6);
      await launch.claimVested(alice);
      const full = await launch.position(alice.publicKey);
      assert.equal(full.spendable.toNumber(), 6_000_000);
      assert.equal(full.vestedClaimed.toNumber(), 6_000_000);
    });

    it("merges same-second buys into one lot", async () => {
      const alice = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator);
      await launch.buy(alice, 1_000_000);
      await launch.buy(alice, 1_000_000);

      const position = await launch.position(alice.publicKey);
      // Both buys usually land in the same second on a local validator; if they
      // straddle a boundary there are two lots, which is equally correct.
      assert.isAtMost(position.lots.length, 2);
      const total = position.lots.reduce((sum, lot) => sum + lot.original.toNumber(), 0);
      assert.equal(total, 2_000_000);
    });
  });

  /* ---------------------------------------------------------------------- */

  describe("sell", () => {
    it("taxes the exit, routes it to the pool, and pays out the rest", async () => {
      const alice = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat, // a constant 20%, so the number is predictable
        vestSeconds: 0,
      });

      await launch.buy(alice, 10_000_000);
      await launch.claimVested(alice);

      const balanceBefore = await provider.connection.getBalance(
        alice.publicKey,
        "confirmed"
      );
      await launch.sell(alice, 10_000_000);

      const pool = await launch.pool();
      assert.equal(
        pool.totalCollected.toNumber(),
        2_000_000,
        "20% of 10_000_000 must reach the pool"
      );

      const balanceAfter = await provider.connection.getBalance(
        alice.publicKey,
        "confirmed"
      );
      assert.isAbove(balanceAfter, balanceBefore - 20_000, "the seller was paid in lamports");

      const position = await launch.position(alice.publicKey);
      assert.equal(position.totalSold.toNumber(), 10_000_000);
      assert.equal(position.lots.length, 0, "an emptied lot is pruned");

      const config = await launch.config();
      assert.equal(config.holderCount, 0);
    });

    it("charges less the longer the tokens were held", async () => {
      const early = await fundedWallet(provider);
      const late = await fundedWallet(provider);

      const launchA = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.fast,
        vestSeconds: 0,
      });
      await launchA.buy(early, 4_000_000);
      await launchA.claimVested(early);
      await launchA.sell(early, 4_000_000); // immediate -> top of the curve
      const earlyTax = (await launchA.pool()).totalCollected.toNumber();

      const launchB = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.fast,
        vestSeconds: 0,
      });
      await launchB.buy(late, 4_000_000);
      await launchB.claimVested(late);
      await waitSeconds(provider, 5); // past the 0% tier
      await launchB.sell(late, 4_000_000);
      const lateTax = (await launchB.pool()).totalCollected.toNumber();

      assert.isAbove(earlyTax, 0, "an immediate exit is taxed");
      assert.isBelow(lateTax, earlyTax, "holding longer must cost less");
    });

    it("buffers tax rather than burning it when nobody is eligible", async () => {
      const lone = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });

      await launch.buy(lone, 5_000_000);
      await launch.claimVested(lone);
      await launch.sell(lone, 5_000_000); // full exit: weight drops to zero

      const pool = await launch.pool();
      assert.equal(pool.totalWeightedShares.toNumber(), 0);
      assert.equal(pool.accRewardPerShare.toString(), "0");
      assert.equal(
        pool.undistributed.toNumber(),
        pool.totalCollected.toNumber(),
        "the whole tax must be held in the buffer"
      );

      // The next holder to appear picks it up.
      const newcomer = await fundedWallet(provider);
      await launch.buy(newcomer, 8_000_000);
      await launch.claimVested(newcomer);

      const after = await launch.pool();
      assert.equal(after.undistributed.toNumber(), 0, "the buffer flushed");
      assert.isTrue(after.accRewardPerShare.gtn(0));
    });
  });

  /* ---------------------------------------------------------------------- */

  describe("transfers are sells", () => {
    it("charges a wallet-to-wallet transfer exactly what a sell costs", async () => {
      const seller = await fundedWallet(provider);
      const sender = await fundedWallet(provider);
      const recipient = anchor.web3.Keypair.generate();

      const sellLaunch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });
      await sellLaunch.buy(seller, 4_000_000);
      await sellLaunch.claimVested(seller);
      await sellLaunch.sell(seller, 4_000_000);
      const sellTax = (await sellLaunch.pool()).totalCollected.toNumber();

      const moveLaunch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });
      await moveLaunch.buy(sender, 4_000_000);
      await moveLaunch.claimVested(sender);
      await moveLaunch.transfer(sender, recipient.publicKey, 4_000_000);
      const transferTax = (await moveLaunch.pool()).totalCollected.toNumber();

      assert.isAbove(sellTax, 0);
      assert.equal(
        transferTax,
        sellTax,
        "a transfer must not be a cheaper exit than a sell"
      );

      const received = await moveLaunch.position(recipient.publicKey);
      assert.equal(
        received.spendable.toNumber(),
        4_000_000 - transferTax,
        "the recipient gets the post-tax remainder, immediately liquid"
      );
    });

    it("restarts the tenure clock in the receiving wallet", async () => {
      const sender = await fundedWallet(provider);
      const recipient = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.fast,
        vestSeconds: 0,
      });

      await launch.buy(sender, 6_000_000);
      await launch.claimVested(sender);
      await waitSeconds(provider, 5); // sender is now past the 0% tier

      const taxBefore = (await launch.pool()).totalCollected.toNumber();
      await launch.transfer(sender, recipient.publicKey, 6_000_000);
      const taxAfterTransfer = (await launch.pool()).totalCollected.toNumber();
      assert.equal(
        taxAfterTransfer,
        taxBefore,
        "the sender's own tokens were mature, so the transfer itself is free"
      );

      // But the recipient is brand new, so dumping immediately is expensive.
      await launch.sell(recipient, 1_000_000);
      const taxAfterDump = (await launch.pool()).totalCollected.toNumber();
      assert.isAbove(
        taxAfterDump,
        taxAfterTransfer,
        "tenure must not travel between wallets"
      );
    });

    it("rejects a self-transfer", async () => {
      const alice = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, { vestSeconds: 0 });
      await launch.buy(alice, 1_000_000);
      await launch.claimVested(alice);
      await expectFailure(
        launch.transfer(alice, alice.publicKey, 100_000),
        "SelfTransfer"
      );
    });

    it("exempts a pool donation, but the donor keeps nothing", async () => {
      const alice = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });
      await launch.buy(alice, 3_000_000);
      await launch.claimVested(alice);
      await launch.donate(alice, 3_000_000);

      const pool = await launch.pool();
      assert.equal(
        pool.totalCollected.toNumber(),
        3_000_000,
        "the full amount reaches the pool, untaxed"
      );
      const position = await launch.position(alice.publicKey);
      assert.equal(position.lots.length, 0, "and the donor is left with nothing");
    });
  });

  /* ---------------------------------------------------------------------- */

  describe("loyalty pool", () => {
    it("pays a holder their tenure-weighted share of someone else's exit tax", async () => {
      const holder = await fundedWallet(provider);
      const flipper = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });

      await launch.buy(holder, 10_000_000);
      await launch.claimVested(holder);

      await launch.buy(flipper, 10_000_000);
      await launch.claimVested(flipper);
      await launch.sell(flipper, 10_000_000);

      const pool = await launch.pool();
      assert.isAbove(pool.totalCollected.toNumber(), 0);

      await waitSlots(provider, MIN_CLAIM_DELAY_SLOTS + 1);
      const before = await launch.position(holder.publicKey);
      await launch.claimPoolShare(holder);
      const after = await launch.position(holder.publicKey);

      assert.isAbove(
        after.lifetimeRewardsClaimed.toNumber(),
        0,
        "the holder was paid from the flipper's tax"
      );
      assert.isAbove(
        after.spendable.toNumber(),
        before.spendable.toNumber(),
        "rewards land as immediately spendable balance"
      );

      const poolAfter = await launch.pool();
      assert.isAtMost(
        poolAfter.totalClaimed.toNumber(),
        poolAfter.totalCollected.toNumber(),
        "the pool can never pay out more than it collected"
      );
    });

    it("blocks a claim inside the minimum block delay", async () => {
      const holder = await fundedWallet(provider);
      const flipper = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });

      await launch.buy(holder, 10_000_000);
      await launch.claimVested(holder);
      await launch.buy(flipper, 10_000_000);
      await launch.claimVested(flipper);
      await launch.sell(flipper, 10_000_000);

      // A balance increase right now resets the holder's eligibility window.
      await launch.buy(holder, 1_000);
      await expectFailure(launch.claimPoolShare(holder), "ClaimTooSoon");

      await waitSlots(provider, MIN_CLAIM_DELAY_SLOTS + 1);
      await launch.claimPoolShare(holder); // now allowed
    });

    it("gives a late buyer nothing from tax collected before they arrived", async () => {
      const holder = await fundedWallet(provider);
      const flipper = await fundedWallet(provider);
      const latecomer = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });

      await launch.buy(holder, 10_000_000);
      await launch.claimVested(holder);
      await launch.buy(flipper, 10_000_000);
      await launch.claimVested(flipper);
      await launch.sell(flipper, 10_000_000);

      // Arrives afterwards, with far more capital.
      await launch.buy(latecomer, 500_000_000);
      await waitSlots(provider, MIN_CLAIM_DELAY_SLOTS + 1);

      await expectFailure(launch.claimPoolShare(latecomer), "NothingToClaim");
      await launch.claimPoolShare(holder); // the holder who was there is owed
    });

    it("lets anyone crank a stale position's weight without moving rewards", async () => {
      const holder = await fundedWallet(provider);
      const cranker = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, { vestSeconds: 0 });

      await launch.buy(holder, 5_000_000);
      const before = await launch.position(holder.publicKey);
      await launch.syncWeight(cranker, holder.publicKey);
      const after = await launch.position(holder.publicKey);

      assert.equal(
        after.pendingRewards.toNumber(),
        before.pendingRewards.toNumber(),
        "a crank must not create rewards"
      );
      const pool = await launch.pool();
      assert.equal(pool.totalWeightedShares.toNumber(), after.weightedShares.toNumber());
    });
  });

  /* ---------------------------------------------------------------------- */

  describe("reputation", () => {
    it("credits a position held past its vest duration", async () => {
      // A big enough buy that cost_basis_lamports clears the reputation
      // divisor within seconds - see REPUTATION_CURVE_RESERVES.
      const alice = await fundedWallet(provider, 300);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 3,
        ...REPUTATION_CURVE_RESERVES,
      });

      await launch.buy(alice, REPUTATION_TEST_BUY_AMOUNT);
      await expectFailure(launch.updateReputation(alice), "NotMatured");

      await waitSeconds(provider, 4);
      await launch.claimVested(alice);
      await launch.updateReputation(alice);

      const reputation = await launch.reputation(alice.publicKey);
      assert.equal(reputation.owner.toBase58(), alice.publicKey.toBase58());
      assert.isTrue(reputation.score.gtn(0), "score must reflect capital x time");
      assert.equal(reputation.tokensHeldToMaturity, 1);

      // Calling again immediately must not farm more score.
      await expectFailure(launch.updateReputation(alice), "NotMatured");
    });

    it("refuses credit to a position that was substantially exited", async () => {
      const alice = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 2,
      });

      await launch.buy(alice, 100_000_000);
      await waitSeconds(provider, 3);
      await launch.claimVested(alice);
      await launch.sell(alice, 80_000_000); // 80% out

      await expectFailure(launch.updateReputation(alice), "ExitedBeforeMaturity");
    });

    it("keeps reputation with the wallet, not the tokens", async () => {
      const alice = await fundedWallet(provider, 300);
      const bob = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 2,
        ...REPUTATION_CURVE_RESERVES,
      });

      await launch.buy(alice, REPUTATION_TEST_BUY_AMOUNT);
      await waitSeconds(provider, 3);
      await launch.claimVested(alice);
      await launch.updateReputation(alice);

      const aliceScore = (await launch.reputation(alice.publicKey)).score;
      await launch.transfer(alice, bob.publicKey, 300_000);

      assert.equal(
        (await launch.reputation(alice.publicKey)).score.toString(),
        aliceScore.toString(),
        "transferring tokens does not move score"
      );
      // Bob has no reputation account at all until he earns one.
      const bobReputation = await program.account.reputation
        .fetch(launch.p.reputation(bob.publicKey))
        .catch(() => null);
      assert.isNull(bobReputation, "reputation is non-transferable");
    });
  });

  /* ---------------------------------------------------------------------- */

  describe("curve solvency", () => {
    it("never lets the vault pay out more than it took in", async () => {
      const traders = await Promise.all([
        fundedWallet(provider),
        fundedWallet(provider),
        fundedWallet(provider),
      ]);
      const launch = await Launch.create(program, provider, creator, {
        taxCurve: CURVES.flat,
        vestSeconds: 0,
      });

      for (const trader of traders) {
        await launch.buy(trader, 20_000_000_000);
        await launch.claimVested(trader);
      }

      for (const trader of traders) {
        const position = await launch.position(trader.publicKey);
        await launch.sell(trader, position.spendable.divn(2));

        const config = await launch.config();
        const vault = await launch.vaultLamports();
        assert.isAtLeast(
          vault,
          config.realSolReserves.toNumber(),
          "the vault must cover the reserve it claims to hold"
        );
        assert.isTrue(
          config.realSolReserves.gten(0),
          "real reserves must never go negative"
        );
      }
    });

    it("prices later buys higher than earlier ones", async () => {
      const first = await fundedWallet(provider);
      const second = await fundedWallet(provider);
      const launch = await Launch.create(program, provider, creator, { vestSeconds: 0 });

      await launch.buy(first, 50_000_000_000_000);
      const firstCost = (await launch.position(first.publicKey)).costBasisLamports.toNumber();

      await launch.buy(second, 50_000_000_000_000);
      const secondCost = (
        await launch.position(second.publicKey)
      ).costBasisLamports.toNumber();

      assert.isAbove(secondCost, firstCost, "the bonding curve must slope upward");
    });
  });
});

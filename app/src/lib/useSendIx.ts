"use client";

import { useCallback, useState } from "react";
import { useConnection, useWallet } from "@solana/wallet-adapter-react";
import { Transaction, type TransactionInstruction, type Signer } from "@solana/web3.js";

export type TxState = {
  busy: boolean;
  signature: string | null;
  error: string | null;
};

const IDLE: TxState = { busy: false, signature: null, error: null };

/**
 * Build -> sign in the wallet -> send -> confirm.
 *
 * All signing happens in the user's wallet extension. This app never holds a
 * private key, and the only extra signers it ever passes are throwaway mint
 * keypairs generated in the browser for a new launch.
 */
export function useSendIx() {
  const { connection } = useConnection();
  const { publicKey, sendTransaction } = useWallet();
  const [state, setState] = useState<TxState>(IDLE);

  const send = useCallback(
    async (
      instructions: TransactionInstruction[],
      options: { signers?: Signer[] } = {}
    ): Promise<string | null> => {
      if (!publicKey) {
        setState({ busy: false, signature: null, error: "Connect a devnet wallet first." });
        return null;
      }
      setState({ busy: true, signature: null, error: null });
      try {
        const transaction = new Transaction().add(...instructions);
        const { blockhash, lastValidBlockHeight } =
          await connection.getLatestBlockhash("confirmed");
        transaction.recentBlockhash = blockhash;
        transaction.feePayer = publicKey;

        const signature = await sendTransaction(transaction, connection, {
          signers: options.signers ?? [],
        });
        await connection.confirmTransaction(
          { signature, blockhash, lastValidBlockHeight },
          "confirmed"
        );
        setState({ busy: false, signature, error: null });
        return signature;
      } catch (cause) {
        setState({
          busy: false,
          signature: null,
          error: explain(cause),
        });
        return null;
      }
    },
    [connection, publicKey, sendTransaction]
  );

  return { ...state, send, reset: () => setState(IDLE) };
}

/** Turn a raw simulation failure into something a person can act on. */
function explain(cause: unknown): string {
  const raw = cause instanceof Error ? cause.message : String(cause);
  const known: [RegExp, string][] = [
    [/ClaimTooSoon/, "Too soon to claim - a few slots must pass after any balance increase."],
    [/NothingToClaim/, "Nothing to claim right now."],
    [/ZeroDonation/, "Enter a donation amount greater than zero."],
    [/NotHoldersAta/, "That token account is not your own canonical associated token account for this mint."],
    [/UnsupportedTokenProgram/, "This mint is owned by a token program this prototype does not support."],
    [/InvalidTokenAccount/, "That token account is missing or uninitialized."],
    [/AccountAlreadyInitialized/, "This wallet is already registered for this token."],
    [/has_one|ConstraintHasOne/, "Wrong signer - this action needs the program's current authority."],
    [/AccountNotInitialized/, "Not registered yet - send the marker amount first, then wait for it to be written."],
    [/insufficient lamports|Attempt to debit/, "Not enough devnet SOL. Airdrop some first."],
    [/User rejected|rejected the request/i, "Rejected in the wallet."],
  ];
  for (const [pattern, message] of known) {
    if (pattern.test(raw)) return message;
  }
  return raw;
}

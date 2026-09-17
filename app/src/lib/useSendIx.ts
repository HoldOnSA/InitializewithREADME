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
    [/InsufficientSpendable/, "Not enough unlocked balance. Claim vested tokens first."],
    [/NothingToClaim/, "Nothing to claim right now."],
    [/LotCapacityExceeded/, "This position is at its lot limit. Compact lots first."],
    [/NotMatured/, "This position has not been held long enough yet."],
    [/ExitedBeforeMaturity/, "Too much of this position was sold for it to count as matured."],
    [/SlippageExceeded/, "Price moved past your limit. Try again."],
    [/TaxCurveNotMonotonic/, "The tax curve may never rise with time held."],
    [/insufficient lamports|Attempt to debit/, "Not enough devnet SOL. Airdrop some first."],
    [/User rejected|rejected the request/i, "Rejected in the wallet."],
  ];
  for (const [pattern, message] of known) {
    if (pattern.test(raw)) return message;
  }
  return raw;
}

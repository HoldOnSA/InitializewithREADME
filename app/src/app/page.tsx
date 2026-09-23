"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useWallet } from "@solana/wallet-adapter-react";
import { PublicKey } from "@solana/web3.js";

import { api, type TokenView } from "@/lib/api";
import { duration, isValidBase58Key, sol } from "@/lib/format";
import { registerMintIx } from "@/lib/program";
import { useSendIx } from "@/lib/useSendIx";
import { Empty, ErrorNote, Spinner, Stat } from "@/components/ui";

export default function HomePage() {
  const [list, setList] = useState<TokenView[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .tokens()
      .then((rows) => !cancelled && setList(rows))
      .catch((cause) => !cancelled && setError(cause));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h1 className="text-hero font-bold text-white">
          A loyalty layer on top of real <span className="lit">pump.fun tokens</span>
        </h1>
        <p className="max-w-3xl text-sm leading-relaxed text-slate-400">
          Trades happen on pump.fun&rsquo;s own program against real, transferable SPL tokens —
          StackApp never touches them. Register a wallet by sending a small, fixed marker
          amount of SOL to a token&rsquo;s deposit address; your weight is your{" "}
          <em className="text-slate-300">live</em> balance × how long you&rsquo;ve held it,
          re-read from chain every time you sync or claim. No wallet ever earns a per-wallet
          bonus for splitting a position, and no balance increase pays out until a few slots
          have passed.
        </p>
        <div className="flex flex-wrap gap-2 text-xs">
          <Link href="/feed" className="btn-ghost">
            Watch the live feed
          </Link>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium uppercase tracking-wider text-slate-500">
          Registered tokens
        </h2>

        {error ? <ErrorNote error={error} /> : null}
        {!list && !error ? <Spinner label="Loading tokens" /> : null}
        {list && list.length === 0 ? (
          <Empty title="No tokens registered yet">
            Register one below, or start the indexer in mock mode to see sample data.
          </Empty>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {list?.map((token) => (
            <Link
              key={token.mint}
              href={`/token/${token.mint}`}
              className="card hover:-translate-y-0.5 hover:border-stack/50"
            >
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-sm text-stack">
                  {token.mint.slice(0, 6)}…{token.mint.slice(-4)}
                </span>
                <span className="text-xs text-slate-500">
                  registered {duration(token.ageSeconds)} ago
                </span>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3">
                <Stat
                  label="Registrations"
                  value={token.registrationCount}
                  hint={
                    token.pendingRegistrationCount
                      ? `${token.pendingRegistrationCount} pending`
                      : undefined
                  }
                />
                <Stat
                  label="Pool"
                  tone="pool"
                  value={sol(token.pool.outstanding)}
                  hint="claimable by holders"
                />
              </div>
            </Link>
          ))}
        </div>
      </section>

      <RegisterToken />
    </div>
  );
}

function RegisterToken() {
  const router = useRouter();
  const { publicKey, connected } = useWallet();
  const { send, busy, error, signature } = useSendIx();
  const [mint, setMint] = useState("");
  const [creator, setCreator] = useState("");

  async function onRegister() {
    if (!publicKey || !isValidBase58Key(mint) || !isValidBase58Key(creator)) return;
    const sig = await send([
      registerMintIx({
        authority: publicKey,
        mint: new PublicKey(mint.trim()),
        creator: new PublicKey(creator.trim()),
      }),
    ]);
    if (sig) router.push(`/token/${mint.trim()}`);
  }

  return (
    <section id="register" className="card space-y-3">
      <h2 className="text-sm font-medium text-slate-200">Register a pump.fun token</h2>
      <p className="text-xs text-slate-500">
        Authority-gated on chain: this only actually succeeds if the wallet you sign with is
        the program&rsquo;s <code className="text-slate-300">GlobalConfig.authority</code>.
        Anyone can try; only the authority&rsquo;s transaction will land.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex-[2]">
          <span className="label">Mint address</span>
          <input
            className="input mt-1"
            placeholder="a real pump.fun mint"
            value={mint}
            onChange={(e) => setMint(e.target.value)}
          />
        </label>
        <label className="flex-[2]">
          <span className="label">Creator (informational only)</span>
          <input
            className="input mt-1"
            placeholder="the pump.fun token's creator"
            value={creator}
            onChange={(e) => setCreator(e.target.value)}
          />
        </label>
        <button
          className="btn-primary"
          disabled={!connected || busy || !isValidBase58Key(mint) || !isValidBase58Key(creator)}
          onClick={onRegister}
        >
          {busy ? "Confirming…" : "Register"}
        </button>
      </div>
      {!connected ? (
        <p className="text-xs text-slate-500">Connect a devnet wallet to continue.</p>
      ) : null}
      {error ? (
        <p className="rounded-lg border border-tax/40 bg-tax/5 px-3 py-2 text-xs text-tax">
          {error}
        </p>
      ) : null}
      {signature ? (
        <p className="text-xs text-stack">
          Confirmed.{" "}
          <a
            className="underline"
            href={`https://explorer.solana.com/tx/${signature}?cluster=devnet`}
            target="_blank"
            rel="noreferrer"
          >
            View on explorer
          </a>
        </p>
      ) : null}
    </section>
  );
}

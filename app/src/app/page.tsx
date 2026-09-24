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
import { ClaimTicker } from "@/components/ClaimTicker";
import { Gem } from "@/components/Gem";
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
      <section className="relative space-y-4 py-6 text-center">
        {/* Second ambient glow layer, scoped to just this hero - the
            page-level glow on `body` (globals.css) is untouched, and this
            doesn't bleed into /feed or /token/[mint]. */}
        <div className="hero-drift pointer-events-none absolute inset-0 -z-20" aria-hidden="true" />

        {/* The gem, centered and responsive (clamp, not a flat size) -
            vertically anchored so its busiest, brightest region (the
            horizontal cross-bar) sits above the heading rather than
            directly behind it; the heading instead overlaps the gem's
            lower half, which narrows to a single point and is visually
            much calmer. */}
        <div
          className="pointer-events-none absolute left-1/2 -top-16 -z-10 -translate-x-1/2 opacity-30"
          aria-hidden="true"
        >
          <Gem
            className="h-[clamp(8rem,26vw,22rem)] w-[clamp(8rem,26vw,22rem)] animate-float"
            glow
          />
        </div>

        <div className="relative mx-auto max-w-3xl">
          {/* Defensive scrim behind just the text block - guarantees
              legibility regardless of exactly how the gem's shape lines
              up at any given breakpoint, rather than relying on the
              positioning above alone. */}
          <div
            className="pointer-events-none absolute -z-[5] inset-0 bg-[radial-gradient(ellipse_70%_65%_at_50%_40%,rgba(3,7,12,0.6),transparent_75%)]"
            aria-hidden="true"
          />

          <ClaimTicker />

          <h1 className="mt-3 text-hero font-bold text-white">
            A loyalty layer on top of real <span className="lit">pump.fun tokens</span>
          </h1>
          <p className="mx-auto mt-3 max-w-2xl text-sm leading-relaxed text-slate-400">
            Trades happen on pump.fun&rsquo;s own program against real, transferable SPL tokens —
            StackApp never touches them. Register a wallet by sending a small, fixed marker
            amount of SOL to a token&rsquo;s deposit address; your weight is your{" "}
            <em className="text-slate-300">live</em> balance × how long you&rsquo;ve held it,
            re-read from chain every time you sync or claim. No wallet ever earns a per-wallet
            bonus for splitting a position, and no balance increase pays out until a few slots
            have passed.
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2 text-xs">
            <Link href="/feed" className="btn-ghost">
              Watch the live feed
            </Link>
          </div>
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

      <HowItWorks />

      <RegisterToken />
    </div>
  );
}

function HowItWorks() {
  const steps = [
    {
      n: "01",
      title: "A token gets tracked, not created",
      body: "StackApp registers a real, already-existing pump.fun token — every trade still happens on pump.fun’s own program, against real, transferable SPL tokens. StackApp never touches them.",
    },
    {
      n: "02",
      title: "You register by sending SOL",
      body: "A small, fixed marker amount, sent straight to that token’s deposit address — a plain transfer, no program interaction required.",
    },
    {
      n: "03",
      title: "Your weight is live balance × time",
      body: "Every sync or claim re-reads your real on-chain balance and multiplies it by how long you’ve continuously held it. Nothing is cached, nothing pays out on a snapshot.",
    },
    {
      n: "04",
      title: "Claim your share, anytime",
      body: "Fees arrive by direct donation or a permissionless pump.fun pull. Your claim pays your weight’s share of everything collected, in SOL, after a short delay past any balance increase.",
    },
  ];

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium uppercase tracking-wider text-slate-500">
        How it works
      </h2>
      <div className="hairline-grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((step) => (
          <div key={step.n} className="hairline-cell space-y-2">
            <span className="font-mono text-xs text-stack">{step.n}</span>
            <p className="text-sm font-medium text-frost">{step.title}</p>
            <p className="text-xs leading-relaxed text-slate-500">{step.body}</p>
          </div>
        ))}
      </div>
    </section>
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
    <section id="register" className="card scroll-mt-20 space-y-3 lg:scroll-mt-0">
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

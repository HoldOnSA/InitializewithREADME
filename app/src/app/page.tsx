"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type TokenView } from "@/lib/api";
import { bps, duration, priceLabel, tokens } from "@/lib/format";
import { Empty, ErrorNote, Spinner, Stat, TaxCurveChart } from "@/components/ui";

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
        <h1 className="text-2xl font-semibold tracking-tight text-white">
          Holding longer is the whole mechanism
        </h1>
        <p className="max-w-3xl text-sm leading-relaxed text-slate-400">
          Every exit is taxed on a curve that decays with how long{" "}
          <em className="text-slate-300">those particular tokens</em> were held — tracked as
          FIFO lots, so topping up cannot hide fresh capital behind an old position. The tax
          funds a loyalty pool paid out by tenure-weighted share. Moving tokens to another
          wallet is taxed exactly like selling them, so splitting a position across wallets
          buys you nothing.
        </p>
        <div className="flex flex-wrap gap-2 text-xs">
          <Link href="/launch" className="btn-primary">
            Launch a token
          </Link>
          <Link href="/feed" className="btn-ghost">
            Watch the live feed
          </Link>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium uppercase tracking-wider text-slate-500">
          Live launches
        </h2>

        {error ? <ErrorNote error={error} /> : null}
        {!list && !error ? <Spinner label="Loading launches" /> : null}
        {list && list.length === 0 ? (
          <Empty title="No launches yet">
            Create one on the <Link href="/launch" className="text-stack hover:underline">launch page</Link>,
            or start the indexer in mock mode to see sample data.
          </Empty>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {list?.map((token) => (
            <Link
              key={token.mint}
              href={`/token/${token.mint}`}
              className="card transition hover:border-stack/50"
            >
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-sm text-stack">
                  {token.mint.slice(0, 6)}…{token.mint.slice(-4)}
                </span>
                <span className="text-xs text-slate-500">
                  {duration(token.ageSeconds)} old
                </span>
              </div>

              <div className="mt-4">
                <TaxCurveChart points={token.taxCurve} />
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3">
                <Stat label="Price" value={priceLabel(token.curve.spotPriceLamports)} />
                <Stat label="Holders" value={token.holderCount} />
                <Stat
                  label="Pool"
                  tone="pool"
                  value={tokens(token.pool.outstanding, token.decimals)}
                  hint="claimable by holders"
                />
                <Stat
                  label="Opening tax"
                  tone="tax"
                  value={bps(token.currentOpeningTaxBps)}
                  hint={`vest ${duration(token.vestDurationSeconds)}`}
                />
              </div>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}

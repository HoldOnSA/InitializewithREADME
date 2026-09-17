"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { api, type PassportView } from "@/lib/api";
import { bps, duration, shortKey, sol, tokens } from "@/lib/format";
import { Empty, ErrorNote, Progress, Spinner, Stat, TierBadge } from "@/components/ui";

const TIER_LADDER = [
  { tier: 0, name: "Drifter", at: 0 },
  { tier: 1, name: "Holder", at: 1_000 },
  { tier: 2, name: "Anchor", at: 10_000 },
  { tier: 3, name: "Keystone", at: 50_000 },
  { tier: 4, name: "Bedrock", at: 250_000 },
];

export default function PassportPage() {
  const params = useParams<{ wallet: string }>();
  const wallet = params.wallet;

  const [passport, setPassport] = useState<PassportView | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .passport(wallet)
        .then((data) => !cancelled && setPassport(data))
        .catch((cause) => !cancelled && setError(cause));
    load();
    const timer = setInterval(load, 8_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [wallet]);

  if (error && !passport) return <ErrorNote error={error} />;
  if (!passport) return <Spinner label="Loading passport" />;

  const score = Number(passport.score);
  const nextAt = passport.nextTierAt ? Number(passport.nextTierAt) : null;
  const currentFloor = TIER_LADDER[Math.min(passport.tier, TIER_LADDER.length - 1)].at;
  const progress = nextAt
    ? ((score - currentFloor) / (nextAt - currentFloor)) * 10_000
    : 10_000;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label">Reputation passport</p>
          <h1 className="font-mono text-xl text-white">{shortKey(wallet, 8, 8)}</h1>
        </div>
        <TierBadge tier={passport.tier} name={passport.tierName} />
      </header>

      <div className="grid gap-4 md:grid-cols-4">
        <div className="card">
          <Stat
            label="Score"
            tone="tier"
            value={score.toLocaleString()}
            hint="milli-SOL-days held to maturity"
          />
        </div>
        <div className="card">
          <Stat label="Held to maturity" value={passport.tokensHeldToMaturity} hint="tokens" />
        </div>
        <div className="card">
          <Stat
            label="Average hold"
            value={duration(passport.averageHoldSeconds)}
            hint={`${passport.activePositions} active position${
              passport.activePositions === 1 ? "" : "s"
            }`}
          />
        </div>
        <div className="card">
          <Stat
            label="Capital at risk"
            tone="stack"
            value={sol(passport.capitalAtRiskLamports)}
            hint="cost basis still in the market"
          />
        </div>
      </div>

      <section className="card space-y-3">
        <div className="flex items-baseline justify-between text-sm">
          <h2 className="font-medium text-slate-200">Progress to the next tier</h2>
          <span className="font-mono text-xs text-slate-500">
            {nextAt
              ? `${score.toLocaleString()} / ${nextAt.toLocaleString()}`
              : "top tier reached"}
          </span>
        </div>
        <Progress value={progress} tone="tier" />
        <ol className="mt-3 grid gap-2 sm:grid-cols-5">
          {TIER_LADDER.map((rung) => {
            const reached = passport.tier >= rung.tier;
            return (
              <li
                key={rung.tier}
                className={`rounded-lg border px-3 py-2 text-xs ${
                  reached ? "border-tier/40 bg-tier/5" : "border-ink-700"
                }`}
              >
                <div className={reached ? "text-tier" : "text-slate-400"}>{rung.name}</div>
                <div className="font-mono text-[11px] text-slate-500">
                  {rung.at.toLocaleString()}+
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="card space-y-2">
        <h2 className="text-sm font-medium text-slate-200">
          What tier {passport.tier} unlocks
        </h2>
        <ul className="space-y-1 text-xs text-slate-400">
          {passport.perks.map((perk) => (
            <li key={perk} className="flex gap-2">
              <span className="text-stack">✓</span>
              {perk}
            </li>
          ))}
        </ul>
        <p className="border-t border-ink-800 pt-2 text-[11px] leading-relaxed text-slate-500">
          Reputation is platform-wide, non-transferable, and earned as{" "}
          <span className="text-slate-300">capital at risk × time held</span> — linear in
          capital and counting no wallets. Splitting a position across wallets leaves total
          score unchanged while lowering every wallet&rsquo;s tier, so it is a pure loss.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium uppercase tracking-wider text-slate-500">
          Positions
        </h2>
        {passport.positions.length === 0 ? (
          <Empty title="No positions">
            This wallet has not bought into any launch yet.
          </Empty>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {passport.positions.map((position) => (
              <Link
                key={position.mint}
                href={`/token/${position.mint}`}
                className="card space-y-3 transition hover:border-stack/50"
              >
                <div className="flex items-baseline justify-between">
                  <span className="font-mono text-sm text-stack">
                    {shortKey(position.mint, 6, 4)}
                  </span>
                  <span className="text-xs text-slate-500">
                    held {duration(position.averageHoldSeconds)}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <Stat label="Holding" value={tokens(position.totalRemaining)} />
                  <Stat
                    label="Unlocked"
                    tone="stack"
                    value={bps(position.unlockProgressBps)}
                  />
                  <Stat
                    label="Claimable"
                    tone="pool"
                    value={tokens(position.projectedClaimable)}
                  />
                </div>
                <Progress value={position.unlockProgressBps} />
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

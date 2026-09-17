"use client";

import type { FeedEvent } from "@/lib/api";
import { bps, sol, tokens } from "@/lib/format";
import { WalletLink } from "@/components/ui";

/** One line of the public feed. Shared by /feed and the token dashboard. */
export function EventLine({ event, decimals = 6 }: { event: FeedEvent; decimals?: number }) {
  const d = event.data;

  switch (event.name) {
    case "BuyExecuted":
      return (
        <span>
          <WalletLink wallet={d.buyer} /> bought{" "}
          <b className="font-mono text-stack">{tokens(d.amount, decimals)}</b> for{" "}
          {sol(d.cost_lamports)}
        </span>
      );

    case "ExitExecuted":
      return (
        <span>
          <WalletLink wallet={d.owner} /> {d.kind_name ?? "sold"}{" "}
          <b className="font-mono">{tokens(d.gross, decimals)}</b>
          {d.tax ? (
            <>
              {" · "}
              <span className="text-tax">{tokens(d.tax, decimals)} tax</span> at{" "}
              {bps(d.top_tax_bps ?? 0)}
            </>
          ) : (
            <span className="text-stack"> · no tax, fully matured</span>
          )}
          {d.kind_name === "transfer" && d.destination ? (
            <>
              {" → "}
              <WalletLink wallet={d.destination} />
            </>
          ) : null}
        </span>
      );

    case "TaxCollected":
      return (
        <span className="text-tax">
          {tokens(d.amount, decimals)} routed to the loyalty pool
          {d.undistributed ? " (buffered — no eligible holders yet)" : ""}
        </span>
      );

    case "PoolClaimed":
      return (
        <span>
          <WalletLink wallet={d.owner} /> claimed{" "}
          <b className="font-mono text-pool">{tokens(d.amount, decimals)}</b> from the pool
        </span>
      );

    case "VestedClaimed":
      return (
        <span>
          <WalletLink wallet={d.owner} /> unlocked{" "}
          <span className="font-mono">{tokens(d.released, decimals)}</span>
        </span>
      );

    case "TierUp":
      return (
        <span className="text-tier">
          <WalletLink wallet={d.owner} /> reached tier {d.new_tier} (from {d.previous_tier})
        </span>
      );

    case "ReputationUpdated":
      return (
        <span>
          <WalletLink wallet={d.owner} /> credited{" "}
          <span className="font-mono text-tier">
            {Number(d.score_delta).toLocaleString()}
          </span>{" "}
          reputation for {sol(d.capital_at_risk_lamports)} held
        </span>
      );

    case "LaunchInitialized":
      return (
        <span>
          <WalletLink wallet={d.creator} /> launched a token · opening tax{" "}
          <span className="text-tax">{bps(d.opening_tax_bps)}</span>
        </span>
      );

    case "WeightSynced":
      return (
        <span className="text-slate-500">
          tenure weight synced {Number(d.previous_weight ?? 0).toLocaleString()} →{" "}
          {Number(d.new_weight ?? 0).toLocaleString()}
        </span>
      );

    case "LotsCompacted":
      return (
        <span className="text-slate-500">
          <WalletLink wallet={d.owner} /> compacted lots ({d.lots_remaining} remaining)
        </span>
      );

    default:
      return <span className="text-slate-500">{event.name}</span>;
  }
}

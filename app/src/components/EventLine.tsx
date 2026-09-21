"use client";

import type { FeedEvent } from "@/lib/api";
import { sol } from "@/lib/format";
import { WalletTag } from "@/components/ui";

/** One line of the public feed. Shared by /feed and the token dashboard. */
export function EventLine({ event }: { event: FeedEvent }) {
  const d = event.data;

  switch (event.name) {
    case "MintRegistered":
      return (
        <span>
          <WalletTag wallet={d.creator} />&rsquo;s token was registered for the loyalty layer
        </span>
      );

    case "WalletRegistered":
      return (
        <span>
          <WalletTag wallet={d.owner} /> registered
        </span>
      );

    case "WeightSynced":
      return (
        <span className="text-slate-500">
          <WalletTag wallet={d.owner} /> weight synced{" "}
          {Number(d.previous_weight ?? 0).toLocaleString()} →{" "}
          {Number(d.new_weight ?? 0).toLocaleString()}
        </span>
      );

    case "FeeCollected":
      return (
        <span className="text-pool">
          {sol(d.amount)} donated to the loyalty pool
          {d.undistributed ? " (buffered — no eligible holders yet)" : ""}
        </span>
      );

    case "RewardClaimed":
      return (
        <span>
          <WalletTag wallet={d.owner} /> claimed{" "}
          <b className="font-mono text-pool">{sol(d.amount)}</b>
        </span>
      );

    default:
      return <span className="text-slate-500">{event.name}</span>;
  }
}

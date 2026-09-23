"use client";

import { useEffect, useState } from "react";

import { api, subscribeFeed, type FeedEvent } from "@/lib/api";
import { shortKey, sol } from "@/lib/format";
import { Gem } from "@/components/Gem";

/**
 * The mockup's "X paid to Y" pill - sourced from real `RewardClaimed`
 * events (the only event that actually names a recipient wallet;
 * `FeeCollected` only has an aggregate amount into the pool, not a "paid
 * to" target). Renders nothing if there's no real claim yet - the mockup's
 * own version fabricates a new random amount/wallet every 4.2s forever,
 * which is exactly what this deliberately does not do.
 */
export function ClaimTicker() {
  const [latest, setLatest] = useState<FeedEvent | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .feed({ kinds: ["RewardClaimed"], limit: 1 })
      .then((rows) => {
        if (!cancelled && rows.length) setLatest(rows[0]);
      })
      .catch(() => {});

    // `subscribeFeed` broadcasts every event kind - api.feed() supports
    // server-side kind filtering, but the live socket doesn't, so this
    // filters client-side instead of forcing a call shape it doesn't have.
    return subscribeFeed((event) => {
      if (event.name === "RewardClaimed") setLatest(event);
    });
  }, []);

  if (!latest) return null;

  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-ink-600 bg-ink-800/80 px-3 py-1.5 text-xs text-slate-400">
      <Gem className="h-4 w-4" />
      <span className="font-mono font-medium tabular-nums text-frost">
        {sol(latest.data.amount)}
      </span>
      paid to
      <span className="font-mono text-stack">{shortKey(String(latest.data.owner ?? ""), 4, 4)}</span>
    </span>
  );
}

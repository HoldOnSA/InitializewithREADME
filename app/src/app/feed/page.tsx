"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { api, subscribeFeed, type FeedEvent } from "@/lib/api";
import { ago, shortKey } from "@/lib/format";
import { EventLine } from "@/components/EventLine";
import { ErrorNote, Spinner } from "@/components/ui";

const FILTERS: { key: string; label: string; kinds: string[] }[] = [
  { key: "all", label: "Everything", kinds: [] },
  { key: "buys", label: "Buys", kinds: ["BuyExecuted"] },
  { key: "exits", label: "Sells & transfers", kinds: ["ExitExecuted"] },
  { key: "tax", label: "Tax → pool", kinds: ["TaxCollected"] },
  { key: "claims", label: "Pool claims", kinds: ["PoolClaimed"] },
  { key: "tiers", label: "Tier-ups", kinds: ["TierUp", "ReputationUpdated"] },
  { key: "launches", label: "Launches", kinds: ["LaunchInitialized"] },
];

const MAX_ROWS = 400;

export default function FeedPage() {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");
  const [filter, setFilter] = useState("all");
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    api
      .feed({ limit: 120 })
      .then(setEvents)
      .catch(setError);

    return subscribeFeed(
      (event) => {
        if (pausedRef.current) return;
        setEvents((prev) => [event, ...prev].slice(0, MAX_ROWS));
      },
      (backlog) => {
        if (pausedRef.current) return;
        setEvents((prev) => (prev.length ? prev : backlog));
      },
      setStatus
    );
  }, []);

  const active = FILTERS.find((f) => f.key === filter) ?? FILTERS[0];
  const rows = useMemo(
    () =>
      active.kinds.length === 0
        ? events
        : events.filter((event) => active.kinds.includes(event.name)),
    [events, active]
  );

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">Live feed</h1>
          <p className="mt-1 text-sm text-slate-400">
            Newest first, straight off the program&rsquo;s event stream.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="flex items-center gap-1.5 text-slate-500">
            <span
              className={`h-2 w-2 rounded-full ${
                status === "open"
                  ? "bg-stack"
                  : status === "connecting"
                    ? "animate-pulse bg-tier"
                    : "bg-tax"
              }`}
            />
            {status === "open" ? "live" : status}
          </span>
          <button className="btn-ghost" onClick={() => setPaused((p) => !p)}>
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
      </header>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            onClick={() => setFilter(option.key)}
            className={`rounded-lg border px-3 py-1.5 text-xs transition ${
              filter === option.key
                ? "border-stack bg-stack/10 text-stack"
                : "border-ink-600 text-slate-400 hover:border-ink-500"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {error ? <ErrorNote error={error} /> : null}
      {!error && events.length === 0 ? <Spinner label="Waiting for events" /> : null}

      <ol className="card divide-y divide-ink-800 p-0">
        {rows.map((event, index) => (
          <li
            key={`${event.signature}-${event.slot}-${index}`}
            className="flex items-baseline gap-3 px-4 py-2 text-xs"
          >
            <span className="w-20 shrink-0 font-mono text-[11px] text-slate-600">
              {event.data.timestamp ? ago(event.data.timestamp) : `slot ${event.slot}`}
            </span>
            <span className="min-w-0 flex-1 text-slate-300">
              <EventLine event={event} decimals={6} />
            </span>
            {event.data.mint ? (
              <span className="hidden shrink-0 font-mono text-[11px] text-slate-600 sm:inline">
                {shortKey(event.data.mint, 4, 3)}
              </span>
            ) : null}
          </li>
        ))}
        {rows.length === 0 && events.length > 0 ? (
          <li className="px-4 py-6 text-center text-xs text-slate-500">
            Nothing matching this filter yet.
          </li>
        ) : null}
      </ol>
    </div>
  );
}

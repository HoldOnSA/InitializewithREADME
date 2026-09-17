"use client";

import Link from "next/link";
import { bps, duration } from "@/lib/format";
import type { TaxCurvePoint } from "@/lib/api";

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "default" | "tax" | "pool" | "stack" | "tier";
}) {
  const toneClass = {
    default: "text-slate-100",
    tax: "text-tax",
    pool: "text-pool",
    stack: "text-stack",
    tier: "text-tier",
  }[tone];
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`font-mono text-lg ${toneClass}`}>{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-slate-500">{hint}</div> : null}
    </div>
  );
}

export function Progress({
  value,
  max = 10_000,
  tone = "stack",
}: {
  value: number;
  max?: number;
  tone?: "stack" | "pool" | "tier";
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const bar = { stack: "bg-stack", pool: "bg-pool", tier: "bg-tier" }[tone];
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-ink-700">
      <div className={`h-full rounded-full ${bar} transition-all`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="card text-center">
      <div className="text-sm font-medium text-slate-300">{title}</div>
      {children ? <div className="mt-2 text-xs text-slate-500">{children}</div> : null}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="card border-tax/40 bg-tax/5">
      <div className="text-sm font-medium text-tax">Could not load</div>
      <p className="mt-1 whitespace-pre-wrap text-xs text-slate-400">{message}</p>
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-ink-600 border-t-stack" />
      {label}…
    </div>
  );
}

/** A small step-chart of the tax curve. */
export function TaxCurveChart({
  points,
  highlightSeconds,
}: {
  points: TaxCurvePoint[];
  highlightSeconds?: number;
}) {
  if (points.length === 0) return null;
  const width = 320;
  const height = 96;
  const maxBps = Math.max(...points.map((p) => p.taxBps), 1);
  const last = points[points.length - 1];
  const span = Math.max(last.secondsHeld * 1.35, 3_600);

  const x = (seconds: number) => (Math.min(seconds, span) / span) * width;
  const y = (taxBps: number) => height - (taxBps / maxBps) * (height - 8) - 4;

  const segments: string[] = [];
  points.forEach((point, i) => {
    const next = points[i + 1];
    const x0 = x(point.secondsHeld);
    const x1 = next ? x(next.secondsHeld) : width;
    const yy = y(point.taxBps);
    segments.push(`${i === 0 ? "M" : "L"}${x0},${yy}`, `L${x1},${yy}`);
    if (next) segments.push(`L${x1},${y(next.taxBps)}`);
  });

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img"
           aria-label="Sell tax against time held">
        <defs>
          <linearGradient id="taxfill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#fb7185" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#fb7185" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={`${segments.join(" ")} L${width},${height} L0,${height} Z`} fill="url(#taxfill)" />
        <path d={segments.join(" ")} fill="none" stroke="#fb7185" strokeWidth="1.75" />
        {highlightSeconds !== undefined ? (
          <line
            x1={x(highlightSeconds)}
            x2={x(highlightSeconds)}
            y1={0}
            y2={height}
            stroke="#4ade80"
            strokeWidth="1"
            strokeDasharray="3 3"
          />
        ) : null}
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>0</span>
        <span>{duration(span / 2)}</span>
        <span>{duration(span)}+</span>
      </div>
    </div>
  );
}

export function TaxCurveTable({ points }: { points: TaxCurvePoint[] }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-slate-500">
          <th className="pb-1 font-normal">Held for</th>
          <th className="pb-1 text-right font-normal">Sell tax</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {points.map((point) => (
          <tr key={point.secondsHeld} className="border-t border-ink-800">
            <td className="py-1">{point.secondsHeld === 0 ? "immediately" : duration(point.secondsHeld)}</td>
            <td className="py-1 text-right text-tax">{bps(point.taxBps)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TierBadge({ tier, name }: { tier: number; name: string }) {
  const tones = [
    "bg-ink-700 text-slate-300",
    "bg-slate-500/20 text-slate-200",
    "bg-pool/20 text-pool",
    "bg-stack/20 text-stack",
    "bg-tier/20 text-tier",
  ];
  return (
    <span className={`pill ${tones[Math.min(tier, tones.length - 1)]}`}>
      Tier {tier} · {name}
    </span>
  );
}

export function MintLink({ mint }: { mint: string }) {
  return (
    <Link href={`/token/${mint}`} className="font-mono text-stack hover:underline">
      {mint.slice(0, 4)}…{mint.slice(-4)}
    </Link>
  );
}

export function WalletLink({ wallet }: { wallet: string }) {
  if (!wallet) return <span className="text-slate-600">-</span>;
  return (
    <Link href={`/passport/${wallet}`} className="font-mono text-slate-300 hover:text-white hover:underline">
      {wallet.slice(0, 4)}…{wallet.slice(-4)}
    </Link>
  );
}

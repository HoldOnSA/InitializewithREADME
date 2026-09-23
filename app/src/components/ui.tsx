"use client";

import Link from "next/link";
import { duration } from "@/lib/format";
import { TENURE_TIER_MULTIPLIER_BPS, TENURE_TIER_SECONDS } from "@/lib/program";

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "default" | "pool" | "stack";
}) {
  const toneClass = {
    default: "text-slate-100",
    pool: "text-pool",
    stack: "text-stack",
  }[tone];
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`font-mono text-lg tabular-nums ${toneClass}`}>{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-slate-500">{hint}</div> : null}
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

/** Held-for / multiplier table, driven by the same constants the program
 * uses - see `lib/program.ts`'s `TENURE_TIER_SECONDS`/`TENURE_TIER_MULTIPLIER_BPS`. */
export function TenureTierTable() {
  const rows = [0, ...TENURE_TIER_SECONDS].map((held, i) => ({
    held,
    mult: TENURE_TIER_MULTIPLIER_BPS[i],
  }));
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-slate-500">
          <th className="pb-1 font-normal">Held for</th>
          <th className="pb-1 text-right font-normal">Multiplier</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {rows.map((row, i) => (
          <tr key={row.held} className="border-t border-ink-800">
            <td className="py-1">{i === 0 ? "less than 1 minute" : duration(row.held)}</td>
            <td className="py-1 text-right tabular-nums text-stack">{(row.mult / 10_000).toFixed(2)}×</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function MintLink({ mint }: { mint: string }) {
  return (
    <Link href={`/token/${mint}`} className="font-mono text-stack hover:underline">
      {mint.slice(0, 4)}…{mint.slice(-4)}
    </Link>
  );
}

/** Not a link - there's no per-wallet page anymore (no reputation passport). */
export function WalletTag({ wallet }: { wallet: string }) {
  if (!wallet) return <span className="text-slate-600">-</span>;
  return (
    <span className="font-mono text-slate-300">
      {wallet.slice(0, 4)}…{wallet.slice(-4)}
    </span>
  );
}

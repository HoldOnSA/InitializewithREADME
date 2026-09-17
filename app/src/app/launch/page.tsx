"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useWallet } from "@solana/wallet-adapter-react";
import { Keypair } from "@solana/web3.js";

import {
  TAX_CURVE_PRESETS,
  initializeLaunchIx,
  validateTaxCurve,
  type TaxPoint,
} from "@/lib/program";
import { useSendIx } from "@/lib/useSendIx";
import { bps, duration } from "@/lib/format";
import { TaxCurveChart, TaxCurveTable } from "@/components/ui";

const VEST_OPTIONS = [
  { label: "None", seconds: 0 },
  { label: "1 day", seconds: 86_400 },
  { label: "3 days", seconds: 259_200 },
  { label: "1 week", seconds: 604_800 },
  { label: "30 days", seconds: 2_592_000 },
  { label: "90 days", seconds: 7_776_000 },
];

export default function LaunchPage() {
  const router = useRouter();
  const { publicKey, connected } = useWallet();
  const { send, busy, signature, error, reset } = useSendIx();

  const [preset, setPreset] = useState<keyof typeof TAX_CURVE_PRESETS>("diamond");
  const [custom, setCustom] = useState<TaxPoint[] | null>(null);
  const [vestSeconds, setVestSeconds] = useState(604_800);
  const [mintKey, setMintKey] = useState<string | null>(null);

  const points = useMemo<TaxPoint[]>(
    () => custom ?? TAX_CURVE_PRESETS[preset].points,
    [custom, preset]
  );
  const curveError = useMemo(() => validateTaxCurve(points), [points]);

  const chartPoints = points.map((p) => ({
    secondsHeld: Number(p.secondsHeld),
    taxBps: p.taxBps,
  }));

  async function onLaunch() {
    if (!publicKey || curveError) return;
    reset();

    // A throwaway keypair is the launch's identity. It is generated in the
    // browser, signs this one transaction, and is then discarded - the program
    // only ever uses the address as a PDA seed.
    const mint = Keypair.generate();
    setMintKey(mint.publicKey.toBase58());

    const instruction = initializeLaunchIx({
      creator: publicKey,
      mint: mint.publicKey,
      taxCurve: points,
      vestDurationSeconds: vestSeconds,
    });

    const sig = await send([instruction]);
    if (sig) {
      router.push(`/token/${mint.publicKey.toBase58()}`);
    }
  }

  function editPoint(index: number, field: "secondsHeld" | "taxBps", raw: string) {
    const next = points.map((p) => ({ ...p }));
    const value = Number(raw);
    if (Number.isNaN(value)) return;
    if (field === "secondsHeld") next[index].secondsHeld = Math.max(0, Math.floor(value));
    else next[index].taxBps = Math.max(0, Math.floor(value));
    setCustom(next);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
      <div className="space-y-6">
        <header>
          <h1 className="text-2xl font-semibold tracking-tight text-white">Launch a token</h1>
          <p className="mt-1 text-sm text-slate-400">
            Creates a <code className="text-slate-300">TokenConfig</code> and{" "}
            <code className="text-slate-300">LoyaltyPool</code> on devnet. You pay rent in test
            SOL; nothing else is charged.
          </p>
        </header>

        <section className="card space-y-4">
          <h2 className="text-sm font-medium text-slate-200">1. Tax curve</h2>
          <div className="grid gap-2 sm:grid-cols-2">
            {Object.entries(TAX_CURVE_PRESETS).map(([key, value]) => (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setPreset(key as keyof typeof TAX_CURVE_PRESETS);
                  setCustom(null);
                }}
                className={`rounded-lg border p-3 text-left transition ${
                  !custom && preset === key
                    ? "border-stack bg-stack/10"
                    : "border-ink-600 hover:border-ink-500"
                }`}
              >
                <div className="text-sm font-medium text-slate-100">{value.label}</div>
                <div className="mt-0.5 text-xs text-slate-500">{value.blurb}</div>
              </button>
            ))}
          </div>

          <details className="text-sm">
            <summary className="cursor-pointer text-xs text-slate-400 hover:text-slate-200">
              Edit points manually
            </summary>
            <div className="mt-3 space-y-2">
              {points.map((point, index) => (
                <div key={index} className="flex items-center gap-2">
                  <input
                    className="input"
                    type="number"
                    min={0}
                    value={Number(point.secondsHeld)}
                    onChange={(e) => editPoint(index, "secondsHeld", e.target.value)}
                    aria-label={`Point ${index + 1} seconds held`}
                  />
                  <span className="text-xs text-slate-500">s →</span>
                  <input
                    className="input"
                    type="number"
                    min={0}
                    max={9000}
                    value={point.taxBps}
                    onChange={(e) => editPoint(index, "taxBps", e.target.value)}
                    aria-label={`Point ${index + 1} tax bps`}
                  />
                  <span className="w-14 text-right text-xs text-tax">{bps(point.taxBps)}</span>
                </div>
              ))}
              {custom ? (
                <button type="button" className="btn-ghost" onClick={() => setCustom(null)}>
                  Reset to preset
                </button>
              ) : null}
            </div>
          </details>

          {curveError ? (
            <p className="rounded-lg border border-tax/40 bg-tax/5 px-3 py-2 text-xs text-tax">
              {curveError}
            </p>
          ) : null}
        </section>

        <section className="card space-y-3">
          <h2 className="text-sm font-medium text-slate-200">2. Vest duration</h2>
          <p className="text-xs text-slate-500">
            Bought tokens unlock linearly over this window. Locked tokens still count as
            capital at risk for pool weight and reputation — they just cannot be sold yet.
          </p>
          <div className="flex flex-wrap gap-2">
            {VEST_OPTIONS.map((option) => (
              <button
                key={option.seconds}
                type="button"
                onClick={() => setVestSeconds(option.seconds)}
                className={`rounded-lg border px-3 py-1.5 text-xs transition ${
                  vestSeconds === option.seconds
                    ? "border-stack bg-stack/10 text-stack"
                    : "border-ink-600 text-slate-400 hover:border-ink-500"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </section>

        <section className="card space-y-3">
          <h2 className="text-sm font-medium text-slate-200">3. Initialize on devnet</h2>
          {!connected ? (
            <p className="text-xs text-slate-500">
              Connect a devnet wallet (Phantom or Solflare, set to Devnet) to continue.
            </p>
          ) : null}
          <button
            type="button"
            className="btn-primary"
            disabled={!connected || busy || !!curveError}
            onClick={onLaunch}
          >
            {busy ? "Confirming…" : "Initialize launch"}
          </button>

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
          {mintKey ? (
            <p className="break-all font-mono text-[11px] text-slate-500">mint {mintKey}</p>
          ) : null}
        </section>
      </div>

      <aside className="space-y-4">
        <div className="card space-y-3">
          <h2 className="text-sm font-medium text-slate-200">Preview</h2>
          <TaxCurveChart points={chartPoints} />
          <TaxCurveTable points={chartPoints} />
          <dl className="space-y-1 border-t border-ink-800 pt-3 text-xs">
            <div className="flex justify-between">
              <dt className="text-slate-500">Vest duration</dt>
              <dd className="font-mono text-slate-300">
                {vestSeconds === 0 ? "none" : duration(vestSeconds)}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Sell immediately</dt>
              <dd className="font-mono text-tax">{bps(points[0]?.taxBps ?? 0)} tax</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-slate-500">Sell at the floor</dt>
              <dd className="font-mono text-stack">
                {bps(points[points.length - 1]?.taxBps ?? 0)} tax
              </dd>
            </div>
          </dl>
        </div>

        <div className="card text-xs leading-relaxed text-slate-500">
          <p className="font-medium text-slate-300">What this does not do</p>
          <p className="mt-1">
            No SPL mint is created and no supply is minted. The prototype keeps balances in
            program-owned <code>Position</code> accounts, which is what makes &ldquo;every
            balance decrease is taxed&rdquo; enforceable at all. A production version would need
            Token-2022 transfer hooks to enforce the same rule on freely-circulating tokens —
            see the README.
          </p>
        </div>
      </aside>
    </div>
  );
}

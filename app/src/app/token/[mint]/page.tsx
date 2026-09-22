"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useConnection, useWallet } from "@solana/wallet-adapter-react";
import { PublicKey } from "@solana/web3.js";

import {
  api,
  type FeedEvent,
  type PendingRegistration,
  type RegistrationView,
  type TokenView,
} from "@/lib/api";
import { duration, shortKey, sol } from "@/lib/format";
import {
  claimIx,
  donateIx,
  reconcileIx,
  REGISTRATION_MARKER_LAMPORTS,
  syncIx,
  writeRegistrationIx,
} from "@/lib/program";
import { useSendIx } from "@/lib/useSendIx";
import { EventLine } from "@/components/EventLine";
import { Empty, ErrorNote, Spinner, Stat, TenureTierTable, WalletTag } from "@/components/ui";

export default function TokenPage() {
  const params = useParams<{ mint: string }>();
  const mint = params.mint;
  const { publicKey, connected } = useWallet();
  const owner = publicKey?.toBase58();

  const [token, setToken] = useState<TokenView | null>(null);
  const [registration, setRegistration] = useState<RegistrationView | null>(null);
  const [registrations, setRegistrations] = useState<RegistrationView[]>([]);
  const [pending, setPending] = useState<PendingRegistration[]>([]);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(async () => {
    try {
      const [tokenView, regRows, pendingRows, feed] = await Promise.all([
        api.token(mint),
        api.registrationsForMint(mint, 20).catch(() => []),
        api.pendingForMint(mint).catch(() => []),
        api.feed({ mint, limit: 15 }).catch(() => []),
      ]);
      setToken(tokenView);
      setRegistrations(regRows);
      setPending(pendingRows);
      setEvents(feed);
      setError(null);
      if (owner) {
        setRegistration(await api.registration(mint, owner).catch(() => null));
      } else {
        setRegistration(null);
      }
    } catch (cause) {
      setError(cause);
    }
  }, [mint, owner]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5_000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (error && !token) return <ErrorNote error={error} />;
  if (!token) return <Spinner label="Loading token" />;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-xl text-white">{shortKey(mint, 8, 8)}</h1>
          <p className="mt-1 text-xs text-slate-500">
            registered {duration(token.ageSeconds)} ago · creator <WalletTag wallet={token.creator} />
          </p>
        </div>
        <a
          className="text-xs text-slate-500 hover:text-slate-300"
          href={`https://explorer.solana.com/address/${mint}?cluster=devnet`}
          target="_blank"
          rel="noreferrer"
        >
          View mint on explorer ↗
        </a>
      </header>

      <div className="grid gap-4 md:grid-cols-4">
        <div className="card">
          <Stat
            label="Registrations"
            value={token.registrationCount}
            hint={
              token.pendingRegistrationCount ? `${token.pendingRegistrationCount} pending` : undefined
            }
          />
        </div>
        <div className="card">
          <Stat
            label="Deposit vault"
            value={sol(token.vaultLamports)}
            hint="markers + donations, minus payouts"
          />
        </div>
        <div className="card">
          <Stat
            label="Loyalty pool"
            value={sol(token.pool.outstanding)}
            tone="pool"
            hint={`${sol(token.pool.totalCollected)} donated all time`}
          />
        </div>
        <div className="card">
          <Stat
            label="Marker amount"
            value={sol(token.registrationMarkerLamports)}
            hint="send this exactly, from your own wallet, to the deposit vault below"
          />
        </div>
      </div>

      {token.pendingReconcileSurplus > 0 ? (
        <ReconcileVault mint={mint} surplus={token.pendingReconcileSurplus} onDone={refresh} />
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-6">
          <YourRegistration
            mint={mint}
            token={token}
            registration={registration}
            connected={connected}
            onDone={refresh}
          />
          {pending.length ? (
            <PendingRegistrations mint={mint} pending={pending} onDone={refresh} />
          ) : null}
          <Registrations registrations={registrations} owner={owner} />
          <RecentActivity events={events} />
        </div>

        <aside className="space-y-4">
          <section className="card space-y-3">
            <h2 className="text-sm font-medium text-slate-200">Tenure tiers</h2>
            <TenureTierTable />
            <p className="text-[11px] leading-relaxed text-slate-500">
              Applied to your live balance, not a snapshot. Splitting a balance across wallets
              sums to the same total weight, never more.
            </p>
          </section>

          <section className="card space-y-2">
            <h2 className="text-sm font-medium text-slate-200">Pool mechanics</h2>
            <dl className="space-y-1 text-xs">
              <Row label="Total donated" value={sol(token.pool.totalCollected)} />
              <Row label="Paid out" value={sol(token.pool.totalClaimed)} />
              <Row label="Awaiting claim" value={sol(token.pool.outstanding)} />
              <Row
                label="Buffered"
                value={sol(token.pool.undistributed)}
                hint="donated while nobody was eligible; never burned"
              />
              <Row
                label="Weighted shares"
                value={token.pool.totalWeightedShares.toLocaleString()}
              />
            </dl>
          </section>
        </aside>
      </div>
    </div>
  );
}

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-t border-ink-800 pt-1 first:border-0">
      <dt className="text-slate-500" title={hint}>
        {label}
      </dt>
      <dd className="font-mono text-slate-300">{value}</dd>
    </div>
  );
}

/** Resolve which token program actually owns `mint`, read directly off the
 * mint account - never assumed. Real pump.fun mints split roughly 14:1
 * Token-2022 vs legacy SPL Token. */
async function resolveTokenProgram(connection: any, mint: PublicKey): Promise<PublicKey> {
  const info = await connection.getAccountInfo(mint);
  if (!info) throw new Error(`mint ${mint.toBase58()} does not exist on chain`);
  return info.owner;
}

function YourRegistration({
  mint,
  token,
  registration,
  connected,
  onDone,
}: {
  mint: string;
  token: TokenView;
  registration: RegistrationView | null;
  connected: boolean;
  onDone: () => void;
}) {
  const { publicKey } = useWallet();
  const { connection } = useConnection();
  const { send, busy, error, signature } = useSendIx();
  const [donateSol, setDonateSol] = useState("0.01");

  async function onSync() {
    if (!publicKey) return;
    const mintKey = new PublicKey(mint);
    const tokenProgram = await resolveTokenProgram(connection, mintKey);
    const sig = await send([
      syncIx({ cranker: publicKey, owner: publicKey, mint: mintKey, tokenProgram }),
    ]);
    if (sig) onDone();
  }

  async function onClaim() {
    if (!publicKey) return;
    const mintKey = new PublicKey(mint);
    const tokenProgram = await resolveTokenProgram(connection, mintKey);
    const sig = await send([claimIx({ owner: publicKey, mint: mintKey, tokenProgram })]);
    if (sig) onDone();
  }

  async function onDonate() {
    if (!publicKey) return;
    const parsed = Number(donateSol);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    const lamports = BigInt(Math.floor(parsed * 1_000_000_000));
    const sig = await send([
      donateIx({ donor: publicKey, mint: new PublicKey(mint), amount: lamports }),
    ]);
    if (sig) onDone();
  }

  if (!connected || !publicKey) {
    return (
      <section className="card">
        <h2 className="text-sm font-medium text-slate-200">Your registration</h2>
        <p className="mt-2 text-xs text-slate-500">
          Connect a devnet wallet to see your registration and sync, claim or donate.
        </p>
      </section>
    );
  }

  return (
    <section className="card space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-slate-200">Your registration</h2>
        {registration ? (
          <span className="text-xs text-slate-500">
            registered {duration(registration.ageSeconds)} ago
          </span>
        ) : null}
      </div>

      {registration ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Registered" value={`${duration(registration.ageSeconds)} ago`} />
          <Stat
            label="Tenure ×"
            tone="stack"
            value={`${(registration.tenureMultiplierBps / 10_000).toFixed(2)}×`}
            hint={`weight ${registration.weightedShares.toLocaleString()}`}
          />
          <Stat
            label="Pool share"
            tone="pool"
            value={`${(registration.poolSharePpm / 10_000).toFixed(4)}%`}
          />
          <Stat
            label="Claimable"
            tone="pool"
            value={sol(registration.projectedClaimable)}
            hint={registration.claimEligible ? "eligible now" : "waiting out the claim delay"}
          />
        </div>
      ) : (
        <p className="text-xs text-slate-500">
          No registration yet. Send the marker amount to the deposit vault below, then wait for
          the operator to write it.
        </p>
      )}

      <div className="space-y-3 border-t border-ink-800 pt-4">
        <p className="text-xs text-slate-500">
          <strong className="text-slate-300">Step 1.</strong> Send exactly{" "}
          {sol(Number(REGISTRATION_MARKER_LAMPORTS))} from your own wallet to the deposit vault
          (<span className="font-mono">{shortKey(token.depositVault, 6, 6)}</span>) — a plain SOL
          transfer, no program interaction. The indexer picks it up and queues it below for the
          operator to write on chain.
        </p>
        <p className="text-xs text-slate-500">
          <strong className="text-slate-300">Step 2.</strong> Once written, sync or claim below —
          both re-read your live balance, so there&rsquo;s nothing else to do after that.
        </p>

        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" disabled={busy} onClick={onSync}>
            Sync my weight
          </button>
          <button className="btn-primary" disabled={busy} onClick={onClaim}>
            Claim my share
          </button>
        </div>

        <details>
          <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">
            Donate SOL to this pool
          </summary>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <label className="flex-1">
              <span className="label">Amount (SOL)</span>
              <input
                className="input mt-1"
                value={donateSol}
                onChange={(e) => setDonateSol(e.target.value)}
                inputMode="decimal"
              />
            </label>
            <button className="btn-ghost" disabled={busy} onClick={onDonate}>
              Donate
            </button>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            The only source of real fee revenue — typically the creator, right after claiming
            their own pump.fun fee normally. Anyone can call this; nobody can earmark a
            donation for themselves.
          </p>
        </details>

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
              View transaction
            </a>
          </p>
        ) : null}
      </div>
    </section>
  );
}

function ReconcileVault({
  mint,
  surplus,
  onDone,
}: {
  mint: string;
  surplus: number;
  onDone: () => void;
}) {
  const { publicKey } = useWallet();
  const { send, busy, error, signature } = useSendIx();

  async function onReconcile() {
    if (!publicKey) return;
    const sig = await send([reconcileIx({ cranker: publicKey, mint: new PublicKey(mint) })]);
    if (sig) onDone();
  }

  return (
    <section className="card space-y-2">
      <h2 className="text-sm font-medium text-slate-200">Unreconciled balance detected</h2>
      <p className="text-xs text-slate-500">
        <span className="font-mono text-frost">{sol(surplus)}</span> is sitting in the deposit
        vault that neither a donation nor the registration-marker bookkeeping explains — most
        likely a plain SOL transfer sent straight to the vault outside{" "}
        <code className="text-slate-300">donate</code>. Fully permissionless: anyone can sweep
        it into the pool as real fee revenue.
      </p>
      <button className="btn-ghost" disabled={busy} onClick={onReconcile}>
        Reconcile vault
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
            View transaction
          </a>
        </p>
      ) : null}
    </section>
  );
}

function PendingRegistrations({
  mint,
  pending,
  onDone,
}: {
  mint: string;
  pending: PendingRegistration[];
  onDone: () => void;
}) {
  const { publicKey } = useWallet();
  const { send, busy } = useSendIx();

  async function write(owner: string) {
    if (!publicKey) return;
    const sig = await send([
      writeRegistrationIx({
        authority: publicKey,
        owner: new PublicKey(owner),
        mint: new PublicKey(mint),
      }),
    ]);
    if (sig) onDone();
  }

  return (
    <section className="card">
      <h2 className="mb-1 text-sm font-medium text-slate-200">Pending registrations</h2>
      <p className="mb-3 text-xs text-slate-500">
        Marker transfers the indexer noticed but hasn&rsquo;t written on chain yet. Only the
        wallet holding <code className="text-slate-300">GlobalConfig.authority</code> can
        actually make these land — see <code className="text-slate-300">SECURITY_NOTES.md</code>.
      </p>
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="pb-1 font-normal">Wallet</th>
            <th className="pb-1 text-right font-normal">Amount</th>
            <th className="pb-1" />
          </tr>
        </thead>
        <tbody>
          {pending.map((p) => (
            <tr key={`${p.mint}-${p.owner}`} className="border-t border-ink-800">
              <td className="py-1">
                <WalletTag wallet={p.owner} />
              </td>
              <td className="py-1 text-right font-mono">{sol(p.amount)}</td>
              <td className="py-1 text-right">
                <button className="btn-ghost" disabled={busy} onClick={() => write(p.owner)}>
                  Write registration
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Registrations({
  registrations,
  owner,
}: {
  registrations: RegistrationView[];
  owner?: string;
}) {
  if (registrations.length === 0) {
    return <Empty title="No registrations yet" />;
  }
  return (
    <section className="card">
      <h2 className="mb-3 text-sm font-medium text-slate-200">Registrations by weight</h2>
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="pb-1 font-normal">Wallet</th>
            <th className="pb-1 text-right font-normal">Weight</th>
            <th className="pb-1 text-right font-normal">Tenure ×</th>
            <th className="pb-1 text-right font-normal">Pool share</th>
          </tr>
        </thead>
        <tbody>
          {registrations.map((r) => (
            <tr
              key={r.owner}
              className={`border-t border-ink-800 ${r.owner === owner ? "bg-stack/5" : ""}`}
            >
              <td className="py-1">
                <WalletTag wallet={r.owner} />
              </td>
              <td className="py-1 text-right font-mono">{r.weightedShares.toLocaleString()}</td>
              <td className="py-1 text-right font-mono text-slate-400">
                {(r.tenureMultiplierBps / 10_000).toFixed(2)}×
              </td>
              <td className="py-1 text-right font-mono text-pool">
                {(r.poolSharePpm / 10_000).toFixed(4)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function RecentActivity({ events }: { events: FeedEvent[] }) {
  if (events.length === 0) return null;
  return (
    <section className="card">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-slate-200">Recent activity</h2>
        <Link href="/feed" className="text-xs text-slate-500 hover:text-slate-300">
          Full feed →
        </Link>
      </div>
      <ul className="space-y-1 text-xs">
        {events.map((event, index) => (
          <li key={`${event.signature}-${index}`} className="border-t border-ink-800 py-1">
            <EventLine event={event} />
          </li>
        ))}
      </ul>
    </section>
  );
}

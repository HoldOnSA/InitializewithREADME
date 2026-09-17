"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useWallet } from "@solana/wallet-adapter-react";
import { PublicKey } from "@solana/web3.js";

import {
  api,
  type FeedEvent,
  type PositionView,
  type TokenView,
} from "@/lib/api";
import {
  bps,
  duration,
  isValidBase58Key,
  priceLabel,
  shortKey,
  sol,
  tokens,
} from "@/lib/format";
import {
  buyIx,
  claimPoolShareIx,
  claimVestedIx,
  sellIx,
  transferPositionIx,
  updateReputationIx,
} from "@/lib/program";
import { useSendIx } from "@/lib/useSendIx";
import { EventLine } from "@/components/EventLine";
import {
  Empty,
  ErrorNote,
  Progress,
  Spinner,
  Stat,
  TaxCurveChart,
  TaxCurveTable,
  WalletLink,
} from "@/components/ui";

const DECIMALS_FALLBACK = 6;

export default function TokenPage() {
  const params = useParams<{ mint: string }>();
  const mint = params.mint;
  const { publicKey, connected } = useWallet();
  const owner = publicKey?.toBase58();

  const [token, setToken] = useState<TokenView | null>(null);
  const [position, setPosition] = useState<PositionView | null>(null);
  const [holders, setHolders] = useState<PositionView[]>([]);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(async () => {
    try {
      const [tokenView, holderRows, feed] = await Promise.all([
        api.token(mint),
        api.holders(mint, 12).catch(() => []),
        api.feed({ mint, limit: 15 }).catch(() => []),
      ]);
      setToken(tokenView);
      setHolders(holderRows);
      setEvents(feed);
      setError(null);
      if (owner) {
        setPosition(await api.position(mint, owner).catch(() => null));
      } else {
        setPosition(null);
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

  const decimals = token.decimals ?? DECIMALS_FALLBACK;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-mono text-xl text-white">{shortKey(mint, 8, 8)}</h1>
          <p className="mt-1 text-xs text-slate-500">
            launched {duration(token.ageSeconds)} ago by <WalletLink wallet={token.creator} /> ·
            vest {token.vestDurationSeconds === 0 ? "none" : duration(token.vestDurationSeconds)}
          </p>
        </div>
        <a
          className="text-xs text-slate-500 hover:text-slate-300"
          href={`https://explorer.solana.com/address/${mint}?cluster=devnet`}
          target="_blank"
          rel="noreferrer"
        >
          View on explorer ↗
        </a>
      </header>

      <div className="grid gap-4 md:grid-cols-4">
        <div className="card">
          <Stat label="Spot price" value={priceLabel(token.curve.spotPriceLamports)} />
        </div>
        <div className="card">
          <Stat
            label="Tokens sold"
            value={tokens(token.curve.tokensSold, decimals)}
            hint={`${token.holderCount} holders`}
          />
        </div>
        <div className="card">
          <Stat
            label="Curve reserves"
            value={sol(token.curve.realSolReserves)}
            tone="stack"
            hint="real SOL backing the curve"
          />
        </div>
        <div className="card">
          <Stat
            label="Loyalty pool"
            value={tokens(token.pool.outstanding, decimals)}
            tone="pool"
            hint={`${tokens(token.pool.totalCollected, decimals)} collected all time`}
          />
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-6">
          <YourPosition
            mint={mint}
            token={token}
            position={position}
            connected={connected}
            onDone={refresh}
          />
          <Holders holders={holders} decimals={decimals} owner={owner} />
          <RecentActivity events={events} decimals={decimals} />
        </div>

        <aside className="space-y-4">
          <section className="card space-y-3">
            <h2 className="text-sm font-medium text-slate-200">Sell tax by time held</h2>
            <TaxCurveChart points={token.taxCurve} />
            <TaxCurveTable points={token.taxCurve} />
            <p className="text-[11px] leading-relaxed text-slate-500">
              Charged per FIFO lot against that lot&rsquo;s own age. A wallet-to-wallet transfer
              pays exactly the same rate.
            </p>
          </section>

          <section className="card space-y-2">
            <h2 className="text-sm font-medium text-slate-200">Pool mechanics</h2>
            <dl className="space-y-1 text-xs">
              <Row label="Total collected" value={tokens(token.pool.totalCollected, decimals)} />
              <Row label="Paid out" value={tokens(token.pool.totalClaimed, decimals)} />
              <Row label="Awaiting claim" value={tokens(token.pool.outstanding, decimals)} />
              <Row
                label="Buffered"
                value={tokens(token.pool.undistributed, decimals)}
                hint="tax collected while nobody was eligible; never burned"
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

function YourPosition({
  mint,
  token,
  position,
  connected,
  onDone,
}: {
  mint: string;
  token: TokenView;
  position: PositionView | null;
  connected: boolean;
  onDone: () => void;
}) {
  const { publicKey } = useWallet();
  const { send, busy, error, signature } = useSendIx();
  const [amount, setAmount] = useState("1000");
  const [recipient, setRecipient] = useState("");
  const decimals = token.decimals ?? DECIMALS_FALLBACK;

  const baseUnits = (() => {
    const parsed = Number(amount);
    if (!Number.isFinite(parsed) || parsed <= 0) return 0n;
    return BigInt(Math.floor(parsed * 10 ** decimals));
  })();

  async function run(build: () => ReturnType<typeof buyIx>) {
    const sig = await send([build()]);
    if (sig) onDone();
  }

  if (!connected || !publicKey) {
    return (
      <section className="card">
        <h2 className="text-sm font-medium text-slate-200">Your position</h2>
        <p className="mt-2 text-xs text-slate-500">
          Connect a devnet wallet to buy, claim vested tokens, or claim your pool share.
        </p>
      </section>
    );
  }

  return (
    <section className="card space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-medium text-slate-200">Your position</h2>
        {position ? (
          <span className="text-xs text-slate-500">
            {position.lots.length} lot{position.lots.length === 1 ? "" : "s"} · first bought{" "}
            {duration(Math.floor(Date.now() / 1000) - position.firstBuyTimestamp)} ago
          </span>
        ) : null}
      </div>

      {position ? (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Holding" value={tokens(position.totalRemaining, decimals)} />
            <Stat
              label="Unlocked"
              tone="stack"
              value={tokens(position.spendable, decimals)}
              hint={`${tokens(position.totalLocked, decimals)} still locked`}
            />
            <Stat
              label="Pool share"
              tone="pool"
              value={`${(position.poolSharePpm / 10_000).toFixed(2)}%`}
              hint={`weight ${position.weightedShares.toLocaleString()}`}
            />
            <Stat
              label="Claimable"
              tone="pool"
              value={tokens(position.projectedClaimable, decimals)}
              hint={position.claimEligible ? "eligible now" : "waiting out the claim delay"}
            />
          </div>

          <div className="space-y-1">
            <div className="flex justify-between text-xs text-slate-500">
              <span>Unlock progress</span>
              <span className="font-mono">{bps(position.unlockProgressBps)}</span>
            </div>
            <Progress value={position.unlockProgressBps} />
          </div>

          {position.lots.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-left text-slate-500">
                  <tr>
                    <th className="pb-1 font-normal">Lot age</th>
                    <th className="pb-1 text-right font-normal">Remaining</th>
                    <th className="pb-1 text-right font-normal">Unlocked</th>
                    <th className="pb-1 text-right font-normal">Sell tax now</th>
                    <th className="pb-1 text-right font-normal">Tenure ×</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {position.lots.map((lot, index) => (
                    <tr key={index} className="border-t border-ink-800">
                      <td className="py-1">{duration(lot.ageSeconds)}</td>
                      <td className="py-1 text-right">{tokens(lot.remaining, decimals)}</td>
                      <td className="py-1 text-right">{bps(lot.vestedBps)}</td>
                      <td className="py-1 text-right text-tax">{bps(lot.currentTaxBps)}</td>
                      <td className="py-1 text-right text-stack">
                        {(lot.tenureMultiplierBps / 10_000).toFixed(2)}×
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-[11px] text-slate-500">
                Oldest lot sells first. Each lot is taxed at its own rate.
              </p>
            </div>
          ) : null}
        </>
      ) : (
        <p className="text-xs text-slate-500">
          No position yet. Buy some tokens below to open one.
        </p>
      )}

      <div className="space-y-3 border-t border-ink-800 pt-4">
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex-1">
            <span className="label">Amount (tokens)</span>
            <input
              className="input mt-1"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
            />
          </label>
          <button
            className="btn-primary"
            disabled={busy || baseUnits === 0n}
            onClick={() => run(() => buyIx({ buyer: publicKey, mint: new PublicKey(mint), amount: baseUnits }))}
          >
            Buy
          </button>
          <button
            className="btn-ghost"
            disabled={busy || baseUnits === 0n || !position?.spendable}
            onClick={() => run(() => sellIx({ seller: publicKey, mint: new PublicKey(mint), amount: baseUnits }))}
          >
            Sell
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            className="btn-ghost"
            disabled={busy || !position?.claimableVestedNow}
            onClick={() => run(() => claimVestedIx({ owner: publicKey, mint: new PublicKey(mint) }))}
          >
            Claim vested
            {position?.claimableVestedNow
              ? ` (${tokens(position.claimableVestedNow, decimals)})`
              : ""}
          </button>
          <button
            className="btn-ghost"
            disabled={busy || !position?.projectedClaimable || !position?.claimEligible}
            onClick={() => run(() => claimPoolShareIx({ owner: publicKey, mint: new PublicKey(mint) }))}
          >
            Claim pool share
          </button>
          <button
            className="btn-ghost"
            disabled={busy || !position}
            onClick={() => run(() => updateReputationIx({ owner: publicKey, mint: new PublicKey(mint) }))}
            title="Credits your platform-wide passport once this position has been held past its vest duration"
          >
            Update reputation
          </button>
        </div>

        <details>
          <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">
            Transfer to another wallet (taxed exactly like a sell)
          </summary>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <label className="flex-1">
              <span className="label">Recipient</span>
              <input
                className="input mt-1"
                placeholder="devnet address"
                value={recipient}
                onChange={(e) => setRecipient(e.target.value)}
              />
            </label>
            <button
              className="btn-ghost"
              disabled={busy || baseUnits === 0n || !isValidBase58Key(recipient)}
              onClick={() =>
                run(() =>
                  transferPositionIx({
                    sender: publicKey,
                    recipient: new PublicKey(recipient.trim()),
                    mint: new PublicKey(mint),
                    amount: baseUnits,
                  })
                )
              }
            >
              Transfer
            </button>
          </div>
          <p className="mt-2 text-[11px] text-slate-500">
            The recipient&rsquo;s tokens arrive in a brand-new lot stamped with the current
            time, so tenure does not travel between wallets.
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

function Holders({
  holders,
  decimals,
  owner,
}: {
  holders: PositionView[];
  decimals: number;
  owner?: string;
}) {
  if (holders.length === 0) {
    return <Empty title="No holders yet" />;
  }
  return (
    <section className="card">
      <h2 className="mb-3 text-sm font-medium text-slate-200">Holders by tenure weight</h2>
      <table className="w-full text-xs">
        <thead className="text-left text-slate-500">
          <tr>
            <th className="pb-1 font-normal">Wallet</th>
            <th className="pb-1 text-right font-normal">Holding</th>
            <th className="pb-1 text-right font-normal">Avg hold</th>
            <th className="pb-1 text-right font-normal">Pool share</th>
          </tr>
        </thead>
        <tbody>
          {holders.map((holder) => (
            <tr
              key={holder.owner}
              className={`border-t border-ink-800 ${
                holder.owner === owner ? "bg-stack/5" : ""
              }`}
            >
              <td className="py-1">
                <WalletLink wallet={holder.owner} />
              </td>
              <td className="py-1 text-right font-mono">
                {tokens(holder.totalRemaining, decimals)}
              </td>
              <td className="py-1 text-right font-mono text-slate-400">
                {duration(holder.averageHoldSeconds)}
              </td>
              <td className="py-1 text-right font-mono text-pool">
                {(holder.poolSharePpm / 10_000).toFixed(2)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function RecentActivity({ events, decimals }: { events: FeedEvent[]; decimals: number }) {
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
            <EventLine event={event} decimals={decimals} />
          </li>
        ))}
      </ul>
    </section>
  );
}

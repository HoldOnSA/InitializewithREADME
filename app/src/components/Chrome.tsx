"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";

// The wallet button touches `window` on mount, so it must not be server-rendered.
const WalletMultiButton = dynamic(
  async () =>
    (await import("@solana/wallet-adapter-react-ui")).WalletMultiButton,
  { ssr: false, loading: () => <div className="h-9 w-36 animate-pulse rounded-lg bg-ink-700" /> }
);

const NAV = [
  { href: "/", label: "Launches" },
  { href: "/launch", label: "Launch" },
  { href: "/feed", label: "Feed" },
];

export function DevnetBanner() {
  return (
    <div className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-1.5 text-center text-xs text-amber-200">
      <strong className="font-semibold">Devnet prototype.</strong> Test SOL only, unaudited,
      no mainnet deployment. Do not use with real funds.
    </div>
  );
}

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-ink-700 bg-ink-950/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
        <Link href="/" className="flex items-center gap-2 font-display font-bold tracking-tight text-frost">
          <span className="grid h-7 w-7 place-items-center rounded-md2 bg-stack text-sm font-bold text-ink-950">
            S
          </span>
          <span>StackApp</span>
        </Link>

        <nav className="flex items-center gap-1 text-sm">
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-full px-3 py-1.5 font-medium transition ${
                  active
                    ? "bg-ink-700 text-frost"
                    : "text-slate-400 hover:bg-ink-800 hover:text-frost"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto">
          <WalletMultiButton />
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-16 border-t border-ink-800 px-4 py-8 text-xs text-slate-500">
      <div className="mx-auto max-w-6xl space-y-1">
        <p>
          StackApp is a devnet prototype of a tenure-weighted launchpad. Going to mainnet
          would require a full security audit and separate legal review before handling any
          real user funds.
        </p>
        <p>Signing happens client-side in your wallet. No private keys are ever sent anywhere.</p>
      </div>
    </footer>
  );
}

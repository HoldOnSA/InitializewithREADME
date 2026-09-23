"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Gem } from "@/components/Gem";

// The wallet button touches `window` on mount, so it must not be server-rendered.
const WalletMultiButton = dynamic(
  async () =>
    (await import("@solana/wallet-adapter-react-ui")).WalletMultiButton,
  { ssr: false, loading: () => <div className="h-9 w-36 animate-pulse rounded-lg bg-ink-700" /> }
);

const ICONS = {
  home: (
    <>
      <path d="M4 11.5 12 4l8 7.5" />
      <path d="M6 10v9h12v-9" />
      <path d="M10 19v-5h4v5" />
    </>
  ),
  feed: <path d="M3 12h4l2.5-6 3 12 2.5-6H21" />,
  register: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 9v6M9 12h6" />
    </>
  ),
};

// Real destinations only - see the sidebar-nav plan discussion for why
// the mockup's Explore/Passport/Curve Lab/Docs items aren't here: none
// of them correspond to a page or anchor that actually exists in the V2
// design. "Register" points at the real `id="register"` form on the
// homepage, not a "Launch" flow - V2 doesn't create tokens, only tracks
// pump.fun ones that already exist.
const NAV = [
  { href: "/", label: "Home", icon: ICONS.home },
  { href: "/feed", label: "Feed", icon: ICONS.feed },
  { href: "/#register", label: "Register", icon: ICONS.register },
];

export function DevnetBanner() {
  return (
    <div className="border-b border-amber-500/20 bg-amber-500/10 px-4 py-1.5 text-center text-xs text-amber-200">
      <strong className="font-semibold">Devnet prototype.</strong> Test SOL only, unaudited,
      no mainnet deployment. Do not use with real funds.
    </div>
  );
}

const Logo = ({ onClick }: { onClick?: () => void }) => (
  <Link
    href="/"
    onClick={onClick}
    className="flex items-center gap-2 font-display font-bold tracking-tight text-frost"
  >
    <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-stack to-pool text-sm font-bold text-ink-950">
      S
    </span>
    <span>StackApp</span>
  </Link>
);

/**
 * Left rail nav, matched from the mockup's `.rail` - persistent on
 * desktop, a slide-out drawer on small screens. Drawer open/closed state
 * is local UI state only, not touching wallet or data logic.
 */
export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  const isActive = (href: string) =>
    href.startsWith("/#") ? false : href === "/" ? pathname === "/" : pathname.startsWith(href);

  // Keyboard escape hatch for the mobile drawer, matching the backdrop click.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <>
      {/* Mobile-only top bar: burger + logo + wallet. Nothing sticky like
          this exists on desktop, so `#register`'s scroll-mt only needs to
          account for this bar's height - see page.tsx. */}
      <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-ink-700 bg-ink-950/80 px-4 py-3 backdrop-blur lg:hidden">
        <button
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-ink-700 text-frost"
          onClick={() => setOpen(true)}
          aria-label="Open navigation"
          aria-expanded={open}
        >
          <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
        <Logo />
        <div className="ml-auto">
          <WalletMultiButton />
        </div>
      </div>

      {open ? (
        <div
          className="fixed inset-0 z-40 bg-black/70 lg:hidden"
          onClick={close}
          aria-hidden="true"
        />
      ) : null}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-56 flex-col border-r border-ink-700 bg-ink-900 p-4 transition-transform duration-200 lg:sticky lg:top-0 lg:z-30 lg:h-screen lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="mb-6 flex items-center justify-between">
          <Logo onClick={close} />
          <button
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-ink-800 hover:text-frost lg:hidden"
            onClick={close}
            aria-label="Close navigation"
          >
            <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <nav className="flex flex-col gap-1">
          {NAV.map((item) => {
            const active = isActive(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={close}
                className={`relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${
                  active
                    ? "bg-ink-700 text-frost"
                    : "text-slate-400 hover:bg-ink-800 hover:text-frost"
                }`}
              >
                {active ? (
                  <span
                    className="absolute -left-1 h-5 w-[3px] rounded-r-full bg-stack"
                    aria-hidden="true"
                  />
                ) : null}
                <svg
                  viewBox="0 0 24 24"
                  className="h-[18px] w-[18px] shrink-0"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.6}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  {item.icon}
                </svg>
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto flex items-center gap-2 border-t border-ink-800 pt-4">
          <Gem className="h-6 w-6" />
          <div className="min-w-0 flex-1">
            <p className="truncate font-display text-xs font-semibold text-frost">StackApp</p>
            <p className="truncate text-[11px] text-slate-500">devnet prototype</p>
          </div>
        </div>
        <div className="mt-3 hidden rounded-xl border border-ink-800 bg-ink-950/40 p-2 lg:block">
          <WalletMultiButton />
        </div>
      </aside>
    </>
  );
}

export function Footer() {
  return (
    <footer className="mt-16 border-t border-ink-800 px-4 py-8 text-xs text-slate-500">
      <div className="mx-auto max-w-6xl space-y-1">
        <p>
          StackApp is a devnet prototype of a loyalty layer on real pump.fun tokens. Going to
          mainnet would require a full security audit and separate legal review before handling
          any real user funds. See <code>SECURITY_NOTES.md</code> in the repo for what is and
          isn&rsquo;t trust-minimized today.
        </p>
        <p>
          Signing happens client-side in your wallet - including for the authority-gated
          actions (register a token, write a registration), which are just signed by whichever
          wallet you connect. No private keys are ever sent anywhere.
        </p>
      </div>
    </footer>
  );
}

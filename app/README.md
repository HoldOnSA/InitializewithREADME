# stackapp-web

Next.js + Tailwind frontend. Devnet only.

> **Optional.** The indexer serves these same four pages itself, in Python, with
> no Node toolchain at all — see `indexer/stackapp_indexer/web/`. That is the
> supported path. This app exists because the original spec asked for it, and it
> is kept in sync as a second front end for anyone who wants the React version.
>
> The JSON API lives under `/api` (the bare paths are the Python-served pages).

```bash
cp .env.local.example .env.local
npm install
npm run dev        # http://localhost:3000
```

It needs the indexer running. For a cold start with no deployed program:

```bash
cd ../indexer && python -m stackapp_indexer --mock
```

## Pages

| Route | What it shows |
|---|---|
| `/` | every live launch, with its tax curve, price and pool size |
| `/launch` | create a `TokenConfig`: tax-curve presets or a hand-edited curve, vest duration, and a live preview |
| `/token/[mint]` | curve state, your position with per-lot unlock progress and current tax, pool size, projected claimable share, holders by tenure weight |
| `/passport/[wallet]` | reputation tier, progress to the next one, tokens held to maturity, average hold time, what the tier unlocks |
| `/feed` | live event feed over WebSocket, newest first, filterable |

## Signing

All signing happens client-side through the wallet adapter (Phantom / Solflare
on devnet). The app never holds a private key. The only extra signer it ever
uses is a throwaway mint keypair generated in the browser for a new launch,
which signs one transaction and is then discarded — the program only ever uses
that address as a PDA seed.

## Instruction building

`src/lib/program.ts` builds instructions directly rather than going through a
generated IDL:

```
data = sha256("global:<snake_case_name>")[0..8] ++ borsh(args)
```

This keeps the client honest about the wire format and avoids shipping an IDL
that can silently drift from the deployed program. The account order in each
builder mirrors the `#[derive(Accounts)]` struct field order exactly — **if you
reorder fields in the Rust, you must reorder them here.**

`validateTaxCurve` mirrors the program's own validation so the launch form can
reject a bad curve before it costs a transaction.

## Notes

- `src/lib/api.ts` is the only thing that talks to the indexer, and it surfaces a
  readable error if the indexer is not running.
- `u128` values arrive as strings; do not `Number()` them without thinking.
- The wallet button is dynamically imported with `ssr: false` — it touches
  `window` on mount.

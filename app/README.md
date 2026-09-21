# stackapp-web

Next.js + Tailwind frontend for StackApp's pump.fun loyalty layer. Devnet only.

> **Optional.** The indexer serves these same pages itself, in Python, with
> no Node toolchain at all — see `indexer/stackapp_indexer/web/`. That is the
> supported path. This app exists as a second front end for anyone who wants
> the React version, and is kept in sync with it.
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
| `/` | every registered token, plus a register-a-token panel |
| `/token/[mint]` | pool, your registration, pending marker candidates, registrations by weight |
| `/feed` | live event feed over WebSocket, newest first, filterable |

StackApp doesn't run the trade for these tokens - pump.fun does, against
real, transferable SPL tokens. There's no bonding curve, tax curve, vesting
or reputation passport here anymore; a registration's weight is just its
live pump.fun balance × how long it's been registered.

## Signing

All signing happens client-side through the wallet adapter (Phantom /
Solflare on devnet). The app never holds a private key - including for
`register_mint` and `write_registration`, which are authority-gated on chain
(`GlobalConfig.authority`) but built and signed exactly like every other
action here: whichever wallet you connect. If that isn't the real authority,
the transaction just fails on chain.

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

`sync`/`claim` need the caller's canonical ATA under whichever token program
actually owns the mint (real pump.fun mints split roughly 14:1 Token-2022 vs
legacy SPL Token) - the token page resolves that by reading the mint
account's owner directly, never assumed, before building either instruction.

## Notes

- `src/lib/api.ts` is the only thing that talks to the indexer, and it surfaces a
  readable error if the indexer is not running.
- `u128` values (`accRewardPerShare`) arrive as strings; do not `Number()` them
  without thinking.
- The wallet button is dynamically imported with `ssr: false` — it touches
  `window` on mount.

/** Display helpers. Everything on the wire is base units or lamports. */

export const LAMPORTS_PER_SOL = 1_000_000_000;

export function sol(lamports: number | bigint, digits = 4): string {
  const value = Number(lamports) / LAMPORTS_PER_SOL;
  if (value !== 0 && Math.abs(value) < 10 ** -digits) return `<0.${"0".repeat(digits - 1)}1 SOL`;
  return `${value.toLocaleString(undefined, { maximumFractionDigits: digits })} SOL`;
}

export function tokens(baseUnits: number | bigint, decimals = 6, digits = 2): string {
  const value = Number(baseUnits) / 10 ** decimals;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(digits)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(digits)}K`;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function bps(value: number, digits = 2): string {
  const pct = value / 100;
  return `${pct.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

export function duration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "-";
  const abs = Math.abs(Math.floor(seconds));
  if (abs === 0) return "0s";
  const units: [number, string][] = [
    [86_400 * 365, "y"],
    [86_400 * 30, "mo"],
    [86_400 * 7, "w"],
    [86_400, "d"],
    [3_600, "h"],
    [60, "m"],
    [1, "s"],
  ];
  const parts: string[] = [];
  let left = abs;
  for (const [size, label] of units) {
    if (left >= size) {
      const count = Math.floor(left / size);
      parts.push(`${count}${label}`);
      left -= count * size;
    }
    if (parts.length === 2) break;
  }
  return parts.join(" ");
}

export function ago(timestamp: number): string {
  const delta = Math.floor(Date.now() / 1000) - timestamp;
  if (delta < 0) return "just now";
  if (delta < 5) return "just now";
  return `${duration(delta)} ago`;
}

export function shortKey(key: string, lead = 4, tail = 4): string {
  if (!key) return "-";
  if (key.length <= lead + tail + 1) return key;
  return `${key.slice(0, lead)}…${key.slice(-tail)}`;
}

export function priceLabel(lamportsPerToken: number): string {
  if (lamportsPerToken >= LAMPORTS_PER_SOL / 1000) {
    return `${(lamportsPerToken / LAMPORTS_PER_SOL).toFixed(6)} SOL`;
  }
  return `${lamportsPerToken.toLocaleString()} lamports`;
}

export function isValidBase58Key(value: string): boolean {
  return /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(value.trim());
}

"use client";

import { useId } from "react";

/**
 * The diamond mark from the mockup - ported directly from its inline
 * `#gem` SVG (same path data, same opacities), not a redrawn approximation.
 * Pure SVG + CSS, no image asset, no npm dependency. `useId()` keeps the
 * gradient/filter ids collision-safe if this ever renders more than once
 * on a page.
 */
export function Gem({
  className = "h-8 w-8",
  glow = false,
}: {
  className?: string;
  glow?: boolean;
}) {
  const gradientId = useId();
  const filterId = useId();

  return (
    <svg viewBox="-100 -80 200 160" className={className} aria-hidden="true">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#bff4ff" />
          <stop offset="55%" stopColor="#35e5ff" />
          <stop offset="100%" stopColor="#1d6fe0" />
        </linearGradient>
        {glow ? (
          <filter id={filterId} x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="5" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        ) : null}
      </defs>
      <g
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeLinejoin="round"
        strokeLinecap="round"
        filter={glow ? `url(#${filterId})` : undefined}
      >
        <path d="M-80,-20 L-40,-58 L40,-58 L80,-20 L0,62 Z" strokeWidth="7" />
        <path d="M-80,-20 H80" strokeWidth="4.5" />
        <path d="M-40,-58 L-52,-20 M40,-58 L52,-20" strokeWidth="3.5" opacity="0.85" />
        <path d="M-40,-58 L-19,-20 M40,-58 L19,-20" strokeWidth="3" opacity="0.6" />
        <path d="M-52,-20 L0,62 M52,-20 L0,62" strokeWidth="3.5" opacity="0.8" />
        <path d="M-19,-20 L0,62 M19,-20 L0,62" strokeWidth="3" opacity="0.55" />
      </g>
    </svg>
  );
}

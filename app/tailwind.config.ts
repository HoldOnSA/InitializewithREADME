import type { Config } from "tailwindcss";

// Color and type tokens matched from the `desktop-ui-real` landing page
// (origin/desktop-ui-real:index.html) - same names as before so every
// existing `bg-ink-*` / `text-stack` / etc. usage across the app just picks
// up the new palette without needing every call site touched.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#03070c", // --void
          900: "#060c14", // --void-2
          800: "#0a121b", // --surface
          700: "#14212e", // --edge
          600: "#1e3547", // --edge-lit
          500: "#44586a", // --slate-dim
        },
        stack: {
          DEFAULT: "#35e5ff", // --ice
          dim: "#1ba9c4", // --ice-dim
        },
        tax: "#ff5d73", // --heat
        pool: "#1d6fe0", // --deep
        tier: "#f5c451", // --gold
        grow: "#3ddc97", // --grow
        frost: "#dcf3fb", // --frost - primary text
      },
      fontFamily: {
        display: ["Chakra Petch", "ui-sans-serif", "system-ui", "sans-serif"],
        body: ["DM Sans", "ui-sans-serif", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      // Heading scale matched from the mockup's h1/h2, `hero` scaled down
      // from its literal clamp(38px,6.2vw,70px) - this app is a dense token
      // dashboard, not a marketing landing page, so the full mockup size
      // would dominate the page above a data grid. `section` is unchanged
      // from the mockup's own h2 scale.
      fontSize: {
        hero: ["clamp(2rem, 5vw, 3.25rem)", { lineHeight: "1.05", letterSpacing: "-0.028em" }],
        section: ["clamp(1.25rem, 2.4vw, 1.625rem)", { lineHeight: "1.15", letterSpacing: "-0.012em" }],
      },
      // The hero gem's idle float, matched from the mockup's `@keyframes
      // float`. `prefers-reduced-motion` handling lives in globals.css.
      // `drift` is this pass's own addition (not from the mockup) for the
      // hero's second ambient glow layer - a slow, subtle translate+scale,
      // not the float's up-down bob.
      keyframes: {
        float: {
          "0%, 100%": { transform: "translateY(-8px)" },
          "50%": { transform: "translateY(8px)" },
        },
        drift: {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(2%, -3%) scale(1.05)" },
        },
      },
      animation: {
        float: "float 9s ease-in-out infinite",
        drift: "drift 24s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;

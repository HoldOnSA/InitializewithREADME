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
    },
  },
  plugins: [],
};

export default config;

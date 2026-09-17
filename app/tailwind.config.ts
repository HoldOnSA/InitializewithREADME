import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07090d",
          900: "#0c1016",
          800: "#141a23",
          700: "#1d2531",
          600: "#2a3442",
          500: "#3b485a",
        },
        stack: {
          DEFAULT: "#4ade80",
          dim: "#166534",
        },
        tax: "#fb7185",
        pool: "#60a5fa",
        tier: "#fbbf24",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;

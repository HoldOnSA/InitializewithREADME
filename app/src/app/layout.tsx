import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { DevnetBanner, Footer, Nav } from "@/components/Chrome";

export const metadata: Metadata = {
  title: "StackApp — tenure-weighted launchpad (devnet)",
  description:
    "A devnet prototype launchpad where holding longer is rewarded and leaving early funds the people who stayed.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <DevnetBanner />
          <Nav />
          <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { DevnetBanner, Footer, Sidebar } from "@/components/Chrome";

export const metadata: Metadata = {
  title: "StackApp — pump.fun loyalty layer (devnet)",
  description:
    "A devnet prototype loyalty layer on real pump.fun tokens: register your wallet, and holding longer earns a bigger tenure-weighted share of creator donations.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <DevnetBanner />
          <div className="lg:flex">
            <Sidebar />
            <div className="min-w-0 flex-1">
              <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
              <Footer />
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}

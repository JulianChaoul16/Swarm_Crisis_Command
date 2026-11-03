import type { Metadata } from "next";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Crisis Command Swarm",
  description: "A human-led agent swarm for Richmond flood operations (simulation)."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* Browser extensions (e.g. Grammarly) inject attributes on <body>. */}
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}

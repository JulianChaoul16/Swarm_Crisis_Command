import type { Metadata } from "next";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Crisis Command",
  description: "Emergency operations dashboard for the Crisis Command simulation.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* Browser extensions (e.g. Grammarly) inject attributes on <body>. */}
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}

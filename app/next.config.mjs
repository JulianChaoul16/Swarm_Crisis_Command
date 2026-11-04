const apiOrigin = process.env.CRISIS_API_ORIGIN ?? "http://127.0.0.1:8000";

import { networkInterfaces } from "node:os";

// This machine's IPv4 addresses, so a phone on the same Wi-Fi can open /report in dev.
const lanAddresses = Object.values(networkInterfaces())
  .flat()
  .filter((address) => address && address.family === "IPv4" && !address.internal)
  .map((address) => address.address);

/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: lanAddresses,
  // Forward dashboard API calls to the Python FastAPI server.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
};

export default nextConfig;

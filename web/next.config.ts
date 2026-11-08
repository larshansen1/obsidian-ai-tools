import type { NextConfig } from "next";

// Proxy /api to `compass serve` so the browser sees one origin (no CORS).
export const PROXY_TIMEOUT_MS = 15 * 60 * 1000;

const nextConfig: NextConfig = {
  // next dev otherwise writes AGENTS.md/CLAUDE.md into web/.
  agentRules: false,
  // Without this, dev chunks are blocked on 127.0.0.1 and the page never hydrates.
  allowedDevOrigins: ["127.0.0.1"],
  // Claims runs can take minutes (reading notes, then a questions call that thinks first). The
  // proxy's 30 s default cut them off with a 500 while the server kept working (#166).
  experimental: { proxyTimeout: PROXY_TIMEOUT_MS },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.COMPASS_API_URL ?? "http://127.0.0.1:8100"}/:path*` }];
  },
};

export default nextConfig;

import type { NextConfig } from "next";

// Proxy chat to the spike FastAPI server so the browser sees one origin.
const nextConfig: NextConfig = {
  // next dev otherwise writes AGENTS.md/CLAUDE.md into web/.
  agentRules: false,
  // Without this, dev chunks are blocked on 127.0.0.1 and the page never hydrates.
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8100/:path*" }];
  },
};

export default nextConfig;

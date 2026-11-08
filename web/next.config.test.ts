import { describe, expect, it } from "vitest";
import nextConfig, { PROXY_TIMEOUT_MS } from "./next.config";

describe("next.config", () => {
  it("lets proxied API calls run for 15 minutes instead of the 30 s default", () => {
    expect(PROXY_TIMEOUT_MS).toBe(900_000);
    expect(nextConfig.experimental?.proxyTimeout).toBe(900_000);
  });
});

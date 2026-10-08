import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchIngests, monthCount, planEvergreen, sendToKai, startDraft, thisMonth } from "./ingest";

const fetchMock = vi.fn();
const JSON_HEADERS = { "Content-Type": "application/json" };

function respond(body: unknown, ok = true, status = 200) {
  fetchMock.mockResolvedValueOnce({ ok, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("ingest api", () => {
  it("posts the source with its topic", async () => {
    respond({ id: "j1" });

    expect(await sendToKai("https://x.org", "X", null)).toEqual({ id: "j1" });
    expect(fetchMock).toHaveBeenCalledWith("/api/ingests", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ url: "https://x.org", title: "X", topic: null }),
    });
  });

  it("throws the API's detail, or the status when there is none", async () => {
    respond({ detail: "kai serve is not running." }, false, 503);
    respond({}, false, 500);

    await expect(fetchIngests()).rejects.toThrow("kai serve is not running.");
    await expect(fetchIngests()).rejects.toThrow("Request failed (500)");
  });

  it("starts a draft and plans the new evergreen", async () => {
    respond({ body: "## Claims" });
    respond({ id: "p1" });

    expect(await startDraft("a b", ["c1", "c2"])).toEqual({ body: "## Claims" });
    expect(await planEvergreen("a b", "T", "## Claims")).toEqual({ id: "p1" });
    expect(fetchMock.mock.calls).toEqual([
      [
        "/api/topics/a%20b/evergreen-draft",
        { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ claim_ids: ["c1", "c2"] }) },
      ],
      [
        "/api/writes/evergreen",
        { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ topic: "a b", title: "T", body: "## Claims" }) },
      ],
    ]);
  });
});

describe("monthCount", () => {
  const log = { jobs: [], per_month: [{ month: "2026-10", count: 3 }, { month: "2026-09", count: 1 }] };

  it("finds the month, or zero", () => {
    expect(monthCount(log, "2026-09")).toBe(1);
    expect(monthCount(log, "2026-08")).toBe(0);
  });
});

describe("thisMonth", () => {
  it("is the UTC month with two digits", () => {
    expect(thisMonth(new Date("2026-02-01T00:30:00Z"))).toBe("2026-02");
    expect(thisMonth(new Date("2026-11-30T23:59:00Z"))).toBe("2026-11");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { dismissBriefing, fetchBriefings, mentions, mergeAdded, withoutLink, type BriefingAdded, type BriefingList, type BriefingRow } from "./briefings";

const fetchMock = vi.fn();

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function row(id: string): BriefingRow {
  return { id, url: `https://example.com/${id}`, title: id, site: "example.com", date: "2026-11-01", section: null, times_seen: 1 };
}

const LIST: BriefingList = {
  added: [],
  groups: [
    { topic_id: "a", topic_name: "A", links: [row("1"), row("2")] },
    { topic_id: null, topic_name: null, links: [row("3")] },
  ],
  pending_checks: 4,
  last_pull: "2026-11-10T12:00:00Z",
};

describe("briefings api", () => {
  it("reads the list", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: () => Promise.resolve(LIST) });
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchBriefings()).toEqual(LIST);
    expect(fetchMock).toHaveBeenCalledWith("/api/briefings");
  });

  it("raises the server's message when the list cannot be read", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({ detail: "topics broken" }) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchBriefings()).rejects.toThrow("topics broken");
  });

  it("falls back to the status when there is no message", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 502, json: () => Promise.reject(new Error("no body")) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchBriefings()).rejects.toThrow("Request failed (502)");
  });

  it("dismisses one link by its encoded id", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);

    await dismissBriefing("a/b");

    expect(fetchMock).toHaveBeenCalledWith("/api/briefings/a%2Fb/dismiss", { method: "POST" });
  });

  it("raises the server's message when a dismiss fails", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 404, json: () => Promise.resolve({ detail: "No such link." }) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(dismissBriefing("x")).rejects.toThrow("No such link.");
  });

  it("falls back to the status when a dismiss fails without a message", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({ detail: 5 }) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(dismissBriefing("x")).rejects.toThrow("Request failed (500)");
  });
});

describe("mentions", () => {
  it("says one briefing or several", () => {
    expect([mentions(1), mentions(2), mentions(12)]).toEqual(["1 briefing", "2 briefings", "12 briefings"]);
  });
});

describe("withoutLink", () => {
  it("drops the link and keeps the rest", () => {
    const next = withoutLink(LIST, "1");

    expect(next.groups.map((g) => g.links.map((l) => l.id))).toEqual([["2"], ["3"]]);
    expect(next.pending_checks).toBe(4);
    expect(next.last_pull).toBe("2026-11-10T12:00:00Z");
  });

  it("drops a group that is left empty", () => {
    const next = withoutLink(LIST, "3");

    expect(next.groups.map((g) => g.topic_id)).toEqual(["a"]);
  });

  it("leaves the list as it was for an unknown id", () => {
    expect(withoutLink(LIST, "9")).toEqual(LIST);
  });
});

function added(id: string, message = "m"): BriefingAdded {
  return { id, title: id, message, note_path: null, finished_at: null };
}

describe("mergeAdded", () => {
  it("puts this visit's links first and keeps each link once", () => {
    const merged = mergeAdded([added("a", "server"), added("b", "server")], [added("c", "local"), added("b", "local")]);

    expect(merged.map((a) => `${a.id}:${a.message}`)).toEqual(["c:local", "b:local", "a:server"]);
  });

  it("is the server list when nothing was added this visit", () => {
    expect(mergeAdded([added("a")], [])).toEqual([added("a")]);
  });
});

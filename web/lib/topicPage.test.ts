import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchTopicPage, isSignalKind, isWindow, signalHref, topicHref } from "./topicPage";

afterEach(() => vi.unstubAllGlobals());

describe("topicPage helpers", () => {
  it("builds the signal and topic links", () => {
    const signal = { topic_id: "a b", topic_name: "A", kind: "new_and_growing", message: "m", note_count: 1 } as const;

    expect(signalHref(signal, "90")).toBe("/topics/a%20b?signal=new_and_growing&window=90");
    expect(topicHref("a b", "all")).toBe("/topics/a%20b?window=all");
  });

  it("accepts only known signal kinds and windows", () => {
    expect(["new_and_growing", "writing_more_than_reading", "source_concentration"].every(isSignalKind)).toBe(true);
    expect(isSignalKind("bogus")).toBe(false);
    expect(isSignalKind(undefined)).toBe(false);
    expect(["30", "90", "all"].every(isWindow)).toBe(true);
    expect(isWindow("7")).toBe(false);
    expect(isWindow(undefined)).toBe(false);
  });

  it("requests the page with window and optional signal", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: 1 }) });
    vi.stubGlobal("fetch", fetchMock);

    await fetchTopicPage("big", "90", null);
    await fetchTopicPage("big", "all", "source_concentration");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/topics/big?window=90");
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/topics/big?window=all&signal=source_concentration");
  });

  it("throws the server's message, or the status when there is none", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 404, json: () => Promise.resolve({ detail: "Unknown topic: x" }) })
      .mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.reject(new Error("no json")) });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchTopicPage("x", "30", null)).rejects.toThrow("Unknown topic: x");
    await expect(fetchTopicPage("x", "30", null)).rejects.toThrow("Request failed (500)");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchTopicPage,
  fetchTopics,
  isSignalKind,
  isWindow,
  signalHref,
  topicFromPath,
  topicHref,
  topicOptionLabel,
} from "./topicPage";

afterEach(() => vi.unstubAllGlobals());

describe("topicPage helpers", () => {
  it("builds the signal and topic links", () => {
    const signal = { topic_id: "a b", topic_name: "A", kind: "new_and_growing", message: "m", note_count: 1 } as const;

    expect(signalHref(signal, "90")).toBe("/topics/a%20b?signal=new_and_growing&window=90");
    expect(topicHref("a b", "all")).toBe("/topics/a%20b?window=all");
  });

  it("accepts only known signal kinds and windows", () => {
    expect(
      ["new_and_growing", "reading_without_writing", "writing_more_than_reading", "source_concentration"].every(
        isSignalKind,
      ),
    ).toBe(true);
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

describe("topic picker helpers", () => {
  it("labels a topic with its note count, singular and with a thousands mark", () => {
    const topic = { id: "a", name: "A", note_count: 1, below_min_notes: true };
    expect(topicOptionLabel(topic)).toBe("A (1 note)");
    expect(topicOptionLabel({ ...topic, note_count: 0 })).toBe("A (0 notes)");
    expect(topicOptionLabel({ ...topic, note_count: 2441 })).toBe("A (2,441 notes)");
  });

  it("reads the topic id from a topic page path only", () => {
    expect(topicFromPath("/topics/big")).toBe("big");
    expect(topicFromPath("/topics/a%20b/")).toBe("a b");
    expect(topicFromPath("/topics/big/claims")).toBeNull();
    expect(topicFromPath("/topics/")).toBeNull();
    expect(topicFromPath("/x/topics/big")).toBeNull();
    expect(topicFromPath("/")).toBeNull();
    expect(topicFromPath(null)).toBeNull();
    expect(topicFromPath("/topics/%E0%A4%A")).toBeNull();
  });

  it("loads the topics, and fails with the status when the request does", async () => {
    const topics = [{ id: "a", name: "A", note_count: 3, below_min_notes: false }];
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ topics }) });
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchTopics()).toEqual(topics);
    expect(fetchMock).toHaveBeenCalledWith("/api/topics");

    fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchTopics()).rejects.toThrow("Request failed (500)");
  });
});

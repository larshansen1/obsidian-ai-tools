import { afterEach, describe, expect, it, vi } from "vitest";
import {
  formatUsd,
  loadHistory,
  noteLabel,
  noteUrl,
  saveHistory,
  scopeFor,
  splitCitations,
} from "./chat";

afterEach(() => vi.unstubAllGlobals());

describe("scopeFor", () => {
  it("reads the topic from a topic page", () => {
    expect(scopeFor("/topics/ai-and-llms")).toEqual({ screen: "topic_page", topic: "ai-and-llms" });
    expect(scopeFor("/topics/a%20b/")).toEqual({ screen: "topic_page", topic: "a b" });
  });
  it("has no topic on the map or elsewhere", () => {
    expect(scopeFor("/")).toEqual({ screen: "topic_map", topic: null });
    expect(scopeFor("/topics")).toEqual({ screen: "other", topic: null });
    expect(scopeFor("/topics/a/b")).toEqual({ screen: "other", topic: null });
  });
});

describe("splitCitations", () => {
  it("returns plain text untouched", () => {
    expect(splitCitations("no notes here")).toEqual([{ kind: "text", text: "no notes here" }]);
  });
  it("splits text around each cited note", () => {
    expect(splitCitations("A [[notes/a.md]] and [[ b.md ]].")).toEqual([
      { kind: "text", text: "A " },
      { kind: "note", path: "notes/a.md" },
      { kind: "text", text: " and " },
      { kind: "note", path: "b.md" },
      { kind: "text", text: "." },
    ]);
  });
  it("handles a citation at the very start and end", () => {
    expect(splitCitations("[[a.md]][[b.md]]")).toEqual([
      { kind: "note", path: "a.md" },
      { kind: "note", path: "b.md" },
    ]);
  });
  it("leaves an unfinished mark as text", () => {
    expect(splitCitations("see [[notes/a")).toEqual([{ kind: "text", text: "see [[notes/a" }]);
  });
});

describe("note links", () => {
  it("opens the note in Obsidian without the .md ending", () => {
    expect(noteUrl("My Vault", "notes/a b.md")).toBe(
      "obsidian://open?vault=My%20Vault&file=notes%2Fa%20b",
    );
  });
  it("labels a note by its file name", () => {
    expect(noteLabel("notes/evergreen/Deep work.md")).toBe("Deep work");
    expect(noteLabel("plain")).toBe("plain");
  });
});

it("formats dollars with two decimals", () => {
  expect(formatUsd(0.5)).toBe("$0.50");
  expect(formatUsd(12)).toBe("$12.00");
});

describe("history", () => {
  it("loads the thread for a topic, or the shared one", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ messages: { a: 1 } }) });
    vi.stubGlobal("fetch", fetchMock);

    expect(await loadHistory("my topic")).toEqual({ a: 1 });
    expect(fetchMock).toHaveBeenLastCalledWith("/api/chat/history?topic=my%20topic");
    await loadHistory(null);
    expect(fetchMock).toHaveBeenLastCalledWith("/api/chat/history");
  });
  it("treats a failed load as no history", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    expect(await loadHistory("t")).toBeNull();
  });
  it("saves the thread under the topic", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await saveHistory("t", { a: 1 });

    expect(fetchMock).toHaveBeenCalledWith("/api/chat/history?topic=t", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: { a: 1 } }),
    });
  });
});

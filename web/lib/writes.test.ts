import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyWrite,
  cancelWrite,
  fetchWrites,
  formatWhen,
  planLinks,
  planTopicTags,
  splitTags,
  undoWrite,
} from "./writes";

const fetchMock = vi.fn();

function respond(ok: boolean, body: unknown, status = 200) {
  fetchMock.mockResolvedValue({ ok, status, json: () => Promise.resolve(body) });
}

const JSON_HEADERS = { "Content-Type": "application/json" };

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("splitTags", () => {
  it("splits on commas and spaces and drops leading #", () => {
    expect(splitTags(" #sleep, rest  ##deep-work,,")).toEqual(["sleep", "rest", "deep-work"]);
    expect(splitTags("  ")).toEqual([]);
  });
});

describe("write requests", () => {
  it("plans links with the evergreen and notes", async () => {
    respond(true, { id: "p1" });

    expect(await planLinks("e.md", ["a.md"])).toEqual({ id: "p1" });
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/links", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ evergreen: "e.md", notes: ["a.md"] }),
    });
  });

  it("plans a topic edit at the topic's tags", async () => {
    respond(true, { id: "p2" });

    await planTopicTags("a b", ["x"], ["y"]);

    expect(fetchMock).toHaveBeenCalledWith("/api/topics/a%20b/tags", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ add: ["x"], remove: ["y"] }),
    });
  });

  it("applies, undoes and lists without a body", async () => {
    respond(true, { id: "l1", status: "written", message: "ok" });

    await applyWrite("p1");
    await undoWrite("l1");
    await fetchWrites();

    expect(fetchMock.mock.calls).toEqual([
      ["/api/writes/p1/apply", { method: "POST", headers: undefined, body: undefined }],
      ["/api/writes/l1/undo", { method: "POST", headers: undefined, body: undefined }],
      ["/api/writes", { method: "GET", headers: undefined, body: undefined }],
    ]);
  });

  it("cancels with DELETE and reports it as cancelled", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204, json: () => Promise.reject(new Error("no body")) });

    expect(await cancelWrite("p1")).toEqual({
      id: "p1",
      status: "cancelled",
      message: "Cancelled. Nothing was written.",
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/p1", { method: "DELETE", headers: undefined, body: undefined });
  });

  it("throws the server's detail, or the status when there is none", async () => {
    respond(false, { detail: "Not an evergreen note: a.md" }, 400);
    await expect(planLinks("a.md", ["b.md"])).rejects.toThrow("Not an evergreen note: a.md");

    fetchMock.mockResolvedValue({ ok: false, status: 500, json: () => Promise.reject(new Error("x")) });
    await expect(fetchWrites()).rejects.toThrow("Request failed (500)");
  });
});

describe("formatWhen", () => {
  it("shows the moment in local time, to the minute", () => {
    const iso = "2026-03-04T05:06:59Z";
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");

    expect(formatWhen(iso)).toBe(
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`,
    );
    expect(formatWhen("2026-03-04T05:06:59")).toBe("2026-03-04 05:06");
  });
});

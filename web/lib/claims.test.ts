import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchClaims, progressText, runClaims, runLabel, saveQuestion } from "./claims";
import { CLAIMS } from "./claimsFixture";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("fetchClaims", () => {
  it("gets the topic's claims, encoding the id", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => CLAIMS });

    expect(await fetchClaims("a b/c")).toEqual(CLAIMS);
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/a%20b%2Fc/claims");
  });

  it("throws the server's message", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404, json: async () => ({ detail: "Unknown topic: x" }) });

    await expect(fetchClaims("x")).rejects.toThrow("Unknown topic: x");
  });

  it("falls back to the status when the error has no readable message", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error("not json");
      },
    });
    await expect(fetchClaims("x")).rejects.toThrow("Request failed (502)");

    fetchMock.mockResolvedValue({ ok: false, status: 422, json: async () => ({ detail: [{ msg: "bad" }] }) });
    await expect(fetchClaims("x")).rejects.toThrow("Request failed (422)");
  });
});

describe("runClaims", () => {
  it("posts whether the user approved going over the limit", async () => {
    const run = { status: "done", message: null, estimate_usd: null, view: CLAIMS };
    fetchMock.mockResolvedValue({ ok: true, json: async () => run });

    expect(await runClaims("big", true)).toEqual(run);
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: '{"approved":true}',
    });
  });

  it("throws on a failed request", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });

    await expect(runClaims("big", false)).rejects.toThrow("Request failed (500)");
  });
});

describe("saveQuestion", () => {
  it("puts the question and returns the new view", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => CLAIMS });

    expect(await saveQuestion("big", "Why?")).toEqual(CLAIMS);
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/question", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: '{"question":"Why?"}',
    });
  });

  it("throws on a rejected question", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 422, json: async () => ({}) });

    await expect(saveQuestion("big", "")).rejects.toThrow("Request failed (422)");
  });
});

describe("runLabel", () => {
  it("is Find claims until a claim exists, then Update claims", () => {
    expect(runLabel({ ...CLAIMS, claim_count: 0 })).toBe("Find claims");
    expect(runLabel({ ...CLAIMS, claim_count: 1 })).toBe("Update claims");
  });
});

describe("progressText", () => {
  it("reports notes read and claims found", () => {
    expect(progressText({ ...CLAIMS, read_notes: 3, pending_notes: 0, claim_count: 9 })).toBe(
      "Read 3 of 3 notes, 9 claims.",
    );
  });

  it("asks to run again only once some notes are read and some are not", () => {
    expect(progressText({ ...CLAIMS, read_notes: 30, pending_notes: 189, claim_count: 100 })).toBe(
      "Read 30 of 219 notes, 100 claims. Run again to read the rest.",
    );
    expect(progressText({ ...CLAIMS, read_notes: 0, pending_notes: 5, claim_count: 0 })).toBe(
      "Read 0 of 5 notes, 0 claims.",
    );
  });
});

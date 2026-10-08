import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CoverageSection } from "./CoverageSection";

const fetchMock = vi.fn();

function respond(ok: boolean, body: unknown, status = 200) {
  fetchMock.mockResolvedValue({ ok, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("CoverageSection", () => {
  it("shows the counts and one bar per type, scaled to the largest", async () => {
    respond(true, {
      topic: "big",
      count: 4,
      notes: 1,
      by_type: [
        { type: "study", count: 3 },
        { type: "talk", count: 1 },
      ],
    });

    const { container } = render(<CoverageSection topic="big" />);

    expect(
      await screen.findByText("4 sources cited in 1 note but not in the vault. Ask the chat what to read next."),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/coverage");
    const bars = Array.from(container.querySelectorAll<HTMLElement>(".coverage-bar"));
    expect(bars.map((b) => b.style.width)).toEqual(["100%", "33.33333333333333%"]);
    expect(Array.from(container.querySelectorAll(".coverage-count")).map((e) => e.textContent)).toEqual(["3", "1"]);
  });

  it("says so when nothing is missing", async () => {
    respond(true, { topic: "big", count: 0, notes: 0, by_type: [] });

    render(<CoverageSection topic="big" />);

    expect(await screen.findByText("Your notes cite no sources that are missing from the vault.")).toBeInTheDocument();
  });

  it("shows the server's message when the request fails", async () => {
    respond(false, { detail: "No topic data yet. Run `compass scan` first." }, 409);

    render(<CoverageSection topic="big" />);

    expect(await screen.findByText("No topic data yet. Run `compass scan` first.")).toBeInTheDocument();
  });

  it("falls back to the status when the error has no message", async () => {
    respond(false, {}, 500);

    render(<CoverageSection topic="big" />);

    expect(await screen.findByText("Request failed (500)")).toBeInTheDocument();
  });

  it("shows loading until the answer arrives", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));

    render(<CoverageSection topic="a b" />);

    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/a%20b/coverage");
  });
});

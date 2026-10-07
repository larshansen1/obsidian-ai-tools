import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopicPageScreen } from "./TopicPageScreen";
import type { TopicPage } from "../lib/topicPage";

const PAGE: TopicPage = {
  window: "30",
  min_notes: 3,
  stats: {
    id: "big",
    name: "Big",
    note_count: 7,
    window_notes: 2,
    momentum: 200,
    evergreens: 2,
    linked_notes: 1,
    notes_per_month: [
      { month: "2026-09", notes: 2 },
      { month: "2026-10", notes: 1 },
    ],
    top_source: { name: "a.com", notes: 2 },
    below_min_notes: false,
    next_step: "Write more.",
  },
  tags: ["x", "x2"],
  top_sources: [
    { name: "a.com", notes: 2 },
    { name: "Zed", notes: 1 },
  ],
  unknown_source_notes: 4,
  source_types: [
    { source_type: "unknown", notes: 4, share: 4 / 7 },
    { source_type: "video", notes: 2, share: 2 / 7 },
  ],
  evergreens: [
    { path: "e1.md", title: "Bridge", created: "2026-10-06", bridges: [{ id: "other", name: "Other" }] },
    { path: "e2.md", title: "Solo", created: null, bridges: [] },
  ],
  recent_notes: [
    { path: "a1.md", title: "A1", created: "2026-09-07", duplicates: [{ path: "a2.md", reason: "source_url" }] },
    { path: "n1.md", title: "N1", created: "2026-06-08", duplicates: [] },
  ],
  signal: null,
};

const fetchMock = vi.fn();

function respondWith(page: TopicPage) {
  fetchMock.mockImplementation((url: string) =>
    url.startsWith("/api/topics/")
      ? Promise.resolve({ ok: true, json: () => Promise.resolve(page) })
      : Promise.resolve({ ok: true, status: 204 }),
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  respondWith(PAGE);
  vi.stubGlobal("fetch", fetchMock);
});

describe("TopicPageScreen", () => {
  it("shows the header counts and tags", async () => {
    render(<TopicPageScreen id="big" window="30" signal={null} />);

    const header = await screen.findByTestId("header");

    expect(header.textContent).toBe("Notes7Momentum+200%Evergreens2Notes with links1Tagsx, x2");
    expect(screen.getByRole("heading", { level: 1, name: "Big" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big?window=30");
  });

  it("counts unknown sources and shows the source type shares", async () => {
    render(<TopicPageScreen id="big" window="30" signal={null} />);

    const sources = await screen.findByTestId("top-sources");

    expect(within(sources).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "a.com (2)",
      "Zed (1)",
      "Unknown source (4)",
    ]);
    expect(within(screen.getByTestId("source-types")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "unknown: 4 (57%)",
      "video: 2 (29%)",
    ]);
  });

  it("shows bridges only for evergreens that have them", async () => {
    render(<TopicPageScreen id="big" window="30" signal={null} />);

    const items = within(await screen.findByTestId("evergreens")).getAllByRole("listitem");

    expect(items.map((li) => li.textContent)).toEqual([
      "Bridge (2026-10-06) bridges to Other",
      "Solo (no date)",
    ]);
  });

  it("marks likely duplicates in recent notes", async () => {
    render(<TopicPageScreen id="big" window="30" signal={null} />);

    const items = within(await screen.findByTestId("recent-notes")).getAllByRole("listitem");

    expect(items.map((li) => li.textContent)).toEqual([
      "A1 (2026-09-07) Likely duplicate of a2.md (same source link)",
      "N1 (2026-06-08)",
    ]);
  });

  it("says so when there are no evergreens", async () => {
    respondWith({ ...PAGE, evergreens: [] });

    render(<TopicPageScreen id="big" window="30" signal={null} />);

    expect(await screen.findByText("None yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("evergreens")).toBeNull();
  });

  it("warns when the topic is below the minimum", async () => {
    respondWith({ ...PAGE, stats: { ...PAGE.stats, below_min_notes: true } });

    render(<TopicPageScreen id="big" window="30" signal={null} />);

    expect(await screen.findByText(/Fewer than 3 notes/)).toBeInTheDocument();
  });

  it("lists the notes behind a signal", async () => {
    respondWith({
      ...PAGE,
      signal: {
        kind: "source_concentration",
        rules: "RULES",
        notes: [{ path: "a1.md", title: "A1", created: "2026-09-07" }],
      },
    });

    render(<TopicPageScreen id="big" window="90" signal="source_concentration" />);

    const panel = await screen.findByTestId("signal-notes");

    expect(panel.textContent).toBe("Notes behind the signalRULESA1 (2026-09-07)");
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big?window=90&signal=source_concentration");
  });

  it("explains a signal that no longer applies", async () => {
    respondWith({ ...PAGE, signal: { kind: "new_and_growing", rules: "RULES", notes: [] } });

    render(<TopicPageScreen id="big" window="30" signal="new_and_growing" />);

    expect(await screen.findByText("This signal no longer applies to the topic.")).toBeInTheDocument();
  });

  it("logs the page view with the topic id", async () => {
    render(<TopicPageScreen id="big" window="30" signal={null} />);
    await screen.findByTestId("header");

    expect(fetchMock).toHaveBeenCalledWith("/api/usage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "screen_view", name: "topic_page", detail: "big" }),
    });
  });

  it("shows the server's message and a way back for an unknown topic", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ detail: "Unknown topic: nope" }) }),
    );

    render(<TopicPageScreen id="nope" window="30" signal={null} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown topic: nope");
    expect(screen.getByRole("link", { name: "Back to topic map" })).toHaveAttribute("href", "/");
  });
});

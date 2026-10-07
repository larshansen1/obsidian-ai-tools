import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TopicMapScreen } from "./TopicMapScreen";
import type { TopicMap, TopicStats } from "../lib/topicMap";

function stats(over: Partial<TopicStats>): TopicStats {
  return {
    id: "big",
    name: "Big",
    note_count: 6,
    window_notes: 2,
    momentum: 200,
    evergreens: 1,
    linked_notes: 1,
    notes_per_month: [{ month: "2026-10", notes: 1 }],
    top_source: { name: "a.com", notes: 2 },
    below_min_notes: false,
    next_step: "Write more.",
    ...over,
  };
}

const MAP: TopicMap = {
  window: "30",
  min_notes: 3,
  momentum_formula: "FORMULA",
  tiles: {
    total_notes: 9,
    notes_last_30: 4,
    notes_prior_30: 1,
    link_share: 0.1111,
    evergreen_count: 1,
    inbox_count: 1,
  },
  topics: [
    stats({}),
    stats({ id: "small", name: "Small", note_count: 1, momentum: null, below_min_notes: true, top_source: null }),
  ],
  signals: [
    {
      topic_id: "big",
      topic_name: "Big",
      kind: "source_concentration",
      message: "Source concentration: 2 notes from one source.",
      note_count: 2,
    },
  ],
};

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation((url: string) => {
    if (url.startsWith("/api/topic-map")) {
      const window = new URL(url, "http://x").searchParams.get("window");
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...MAP, window }) });
    }
    return Promise.resolve({ ok: true, status: 204 });
  });
  vi.stubGlobal("fetch", fetchMock);
});

describe("TopicMapScreen", () => {
  it("shows the summary tiles from the API", async () => {
    render(<TopicMapScreen />);

    const tiles = await screen.findAllByTestId("tile");

    expect(tiles.map((t) => t.textContent)).toEqual([
      "Notes, last 30 days4+300% vs 1 in the 30 days before",
      "Notes with links11%of 9 notes",
      "Evergreens1",
      "Inbox1",
    ]);
    expect(fetchMock).toHaveBeenCalledWith("/api/topic-map?window=30");
  });

  it("updates the panel when a bubble is clicked, without reloading", async () => {
    render(<TopicMapScreen />);
    await screen.findByTestId("bubble-big");
    expect(screen.queryByTestId("topic-panel")).toBeNull();

    fireEvent.click(screen.getByTestId("bubble-big"));
    const panel = screen.getByTestId("topic-panel");
    expect(within(panel).getByRole("heading", { name: "Big" })).toBeInTheDocument();
    expect(within(panel).getByText("+200%")).toBeInTheDocument();
    expect(within(panel).getByText("a.com (2)")).toBeInTheDocument();
    expect(within(panel).getByText(/Write more\./)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open topic page" })).toHaveAttribute("href", "/topics/big?window=30");

    fireEvent.click(screen.getByTestId("bubble-small"));
    const next = screen.getByTestId("topic-panel");
    expect(within(next).getByRole("heading", { name: "Small" })).toBeInTheDocument();
    expect(within(next).getByText("n/a")).toBeInTheDocument();
    expect(within(next).getByText(/Fewer than 3 notes/)).toBeInTheDocument();
    expect(within(next).getByText("none")).toBeInTheDocument();
  });

  it("reloads the data when the window changes", async () => {
    render(<TopicMapScreen />);
    await screen.findByTestId("bubble-big");

    fireEvent.click(screen.getByRole("button", { name: "90 days" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/topic-map?window=90"));
    expect(screen.getByRole("button", { name: "90 days" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "false");
  });

  it("lists the signals and links each to the notes behind it", async () => {
    render(<TopicMapScreen />);

    const items = await screen.findAllByTestId("signal");

    expect(items.map((i) => i.textContent)).toEqual([
      "Big: Source concentration: 2 notes from one source. See the 2 notes",
    ]);
    expect(within(items[0]).getByRole("link", { name: "See the 2 notes" })).toHaveAttribute(
      "href",
      "/topics/big?signal=source_concentration&window=30",
    );
  });

  it("says so when there are no signals", async () => {
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...MAP, signals: [], window: new URL(url, "http://x").searchParams.get("window") }),
      }),
    );

    render(<TopicMapScreen />);

    expect(await screen.findByTestId("no-signals")).toHaveTextContent("No signals right now.");
    expect(screen.queryAllByTestId("signal")).toEqual([]);
  });

  it("logs when a signal is opened and links the topic page with the chosen window", async () => {
    render(<TopicMapScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "90 days" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/topic-map?window=90"));

    fireEvent.click(await screen.findByRole("link", { name: "See the 2 notes" }));

    expect(screen.getByRole("link", { name: "See the 2 notes" })).toHaveAttribute(
      "href",
      "/topics/big?signal=source_concentration&window=90",
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/usage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "action", name: "open_signal", detail: "big:source_concentration" }),
    });
  });

  it("explains what the evergreen axis means", async () => {
    render(<TopicMapScreen />);

    expect((await screen.findByTestId("legend")).textContent).toBe(
      "Up: evergreens. An evergreen is a note of your own thinking, kept in the notes/evergreen folder (hypotheses count too). A topic counts the evergreens that carry its tags. More evergreens means you have worked the topic through. Links are shown separately. Across: momentum. Size: number of notes.",
    );
  });

  it("shows the formula", async () => {
    render(<TopicMapScreen />);

    expect(await screen.findByText("FORMULA")).toBeInTheDocument();
  });

  it("logs the screen view and a topic selection", async () => {
    render(<TopicMapScreen />);
    await screen.findByTestId("bubble-big");

    fireEvent.click(screen.getByTestId("bubble-big"));

    const post = (kind: string, name: string, detail: string | null) => [
      "/api/usage",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, name, detail }),
      },
    ];
    expect(fetchMock).toHaveBeenCalledWith(...post("screen_view", "topic_map", null));
    expect(fetchMock).toHaveBeenCalledWith(...post("action", "select_topic", "big"));
  });

  it("shows the server's message when there is no data yet", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ detail: "No topic data yet." }) }),
    );

    render(<TopicMapScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("No topic data yet.");
  });
});

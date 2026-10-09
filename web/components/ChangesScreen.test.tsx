import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChangesScreen } from "./ChangesScreen";
import { job } from "../lib/ingestFixture";
import { formatWhen } from "../lib/writes";

const fetchMock = vi.fn();

const WRITES = [
  {
    id: "l2",
    kind: "edit_topic",
    summary: "Topic Big: add sleep",
    written_at: "2026-10-08T12:30:00",
    files: [".kai/topics.yaml"],
    undone: false,
  },
  {
    id: "l1",
    kind: "link_notes",
    summary: "Link 2 notes to [[Ev]]",
    written_at: "2026-10-08T12:00:00",
    files: ["notes/a.md", "notes/b.md"],
    undone: true,
  },
];

type Reply = { ok: boolean; status: number; json: () => Promise<unknown> };

function reply(body: unknown, ok = true, status = 200): Reply {
  return { ok, status, json: () => Promise.resolve(body) };
}

// Answers in order, except the kai log, which has its own answer.
let queue: Reply[] = [];
let ingests: Reply = reply({ jobs: [], per_month: [] });

function respond(body: unknown, ok = true, status = 200) {
  queue.push(reply(body, ok, status));
}

beforeEach(() => {
  queue = [];
  ingests = reply({ jobs: [], per_month: [] });
  fetchMock.mockReset();
  fetchMock.mockImplementation((url: string) => Promise.resolve(url === "/api/ingests" ? ingests : queue.shift()));
  vi.stubGlobal("fetch", fetchMock);
});

describe("ChangesScreen", () => {
  it("lists the logged writes with Undo for the ones not undone", async () => {
    respond(WRITES);
    render(<ChangesScreen />);

    const items = await screen.findAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      `Topic Big: add sleep ${formatWhen(WRITES[0].written_at)} · .kai/topics.yaml Undo`,
      `Link 2 notes to [[Ev]] ${formatWhen(WRITES[1].written_at)} · notes/a.md, notes/b.md (undone)`,
    ]);
    expect(fetchMock).toHaveBeenCalledWith("/api/writes", { method: "GET", headers: undefined, body: undefined });
  });

  it("undoes a write and reads the list again", async () => {
    respond(WRITES);
    respond({ id: "u1", status: "undone", message: "Undone: Topic Big: add sleep" });
    respond([{ ...WRITES[0], undone: true }]);
    render(<ChangesScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Undone: Topic Big: add sleep");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Undo" })).toBeNull());
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/l2/undo", { method: "POST", headers: undefined, body: undefined });
  });

  it("shows an undo error", async () => {
    respond(WRITES);
    respond({ detail: "This write was already undone." }, false, 409);
    render(<ChangesScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));

    expect(await screen.findByRole("status")).toHaveTextContent("This write was already undone.");
  });

  it("says when there are no changes, and shows a load error", async () => {
    respond([]);
    const { unmount } = render(<ChangesScreen />);
    expect(await screen.findByText("No changes yet. Links, topic edits and new evergreens show up here.")).toBeInTheDocument();
    unmount();

    respond({ detail: "boom" }, false, 500);
    render(<ChangesScreen />);
    expect(await screen.findByRole("status")).toHaveTextContent("boom");
  });

  it("shows what was sent to kai and this month's count of new notes", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-10-08T12:00:00Z") });
    ingests = reply({
      jobs: [
        job({ id: "j2", title: "Talk", status: "failed", message: "kai could not ingest it: 404" }),
        job({ id: "j1", status: "done", message: "Added to the vault: Paper", requested_at: "2026-10-08T11:00:00Z" }),
      ],
      per_month: [
        { month: "2026-10", count: 2 },
        { month: "2026-09", count: 5 },
      ],
    });
    respond([]);
    render(<ChangesScreen />);

    const sent = within(await screen.findByTestId("ingests"));
    expect(screen.getByTestId("ingest-count").textContent).toBe("New notes from suggestions this month: 2");
    expect(screen.getByText("2026-10: 2 · 2026-09: 5")).toBeInTheDocument();
    expect(sent.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      `Talk Failed: kai could not ingest it: 404 · ${formatWhen("2026-10-08T12:00:00Z")}`,
      `Paper Done: Added to the vault: Paper · ${formatWhen("2026-10-08T11:00:00Z")}`,
    ]);
    expect(fetchMock).toHaveBeenCalledWith("/api/ingests", { method: "GET", headers: undefined, body: undefined });
    vi.useRealTimers();
  });

  it("counts zero in a month with no new notes, and says when nothing was sent", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-11-01T00:00:00Z") });
    ingests = reply({ jobs: [], per_month: [{ month: "2026-10", count: 2 }] });
    respond([]);
    render(<ChangesScreen />);

    expect(await screen.findByTestId("ingest-count")).toHaveTextContent("New notes from suggestions this month: 0");
    expect(screen.queryByText("2026-10: 2")).toBeNull();
    expect(screen.getByText("Nothing sent yet. Use Ingest on a suggested source in the chat.")).toBeInTheDocument();
    vi.useRealTimers();
  });
});

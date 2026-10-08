import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChangesScreen } from "./ChangesScreen";
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

function respond(body: unknown, ok = true, status = 200) {
  fetchMock.mockResolvedValueOnce({ ok, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  fetchMock.mockReset();
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
    expect(await screen.findByText("No changes yet. Links and topic edits show up here.")).toBeInTheDocument();
    unmount();

    respond({ detail: "boom" }, false, 500);
    render(<ChangesScreen />);
    expect(await screen.findByRole("status")).toHaveTextContent("boom");
  });
});

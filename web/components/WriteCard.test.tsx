import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WriteCard } from "./WriteCard";
import type { PendingWrite } from "../lib/writes";

const fetchMock = vi.fn();

const PENDING: PendingWrite = {
  id: "p1",
  kind: "link_notes",
  summary: "Link 1 note to [[E]]",
  files: [
    {
      file: "notes/a.md",
      lines: [
        { op: " ", text: "Body" },
        { op: "-", text: "old" },
        { op: "@", text: "" },
        { op: "+", text: "- [[E]]" },
      ],
    },
  ],
};

const NO_BODY = { method: "POST", headers: undefined, body: undefined };

function respond(body: unknown, ok = true, status = 200) {
  fetchMock.mockResolvedValueOnce({ ok, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("WriteCard", () => {
  it("shows the exact lines and writes nothing before a click", () => {
    const { container } = render(<WriteCard pending={PENDING} onResult={vi.fn()} />);

    expect(screen.getByText("Link 1 note to [[E]]")).toBeInTheDocument();
    expect(screen.getByText("notes/a.md")).toBeInTheDocument();
    expect(Array.from(container.querySelectorAll(".write-line")).map((e) => [e.className, e.textContent])).toEqual([
      ["write-line write-line-same", "  Body"],
      ["write-line write-line-del", "- old"],
      ["write-line write-line-gap", "… "],
      ["write-line write-line-add", "+ - [[E]]"],
    ]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("applies on Approve and hands back the outcome", async () => {
    const outcome = { id: "l1", status: "written", message: "Link 1 note to [[E]]" };
    respond(outcome);
    const onResult = vi.fn();
    render(<WriteCard pending={PENDING} onResult={onResult} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(onResult).toHaveBeenCalledWith(outcome));
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/p1/apply", NO_BODY);
  });

  it("cancels on Cancel", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 204, json: () => Promise.resolve(null) });
    const onResult = vi.fn();
    render(<WriteCard pending={PENDING} onResult={onResult} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(onResult).toHaveBeenCalledWith({ id: "p1", status: "cancelled", message: "Cancelled. Nothing was written." }),
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/p1", { method: "DELETE", headers: undefined, body: undefined });
  });

  it("offers Undo after a write and reports the undo", async () => {
    respond({ id: "u1", status: "undone", message: "Undone: Link 1 note to [[E]]" });
    const onUndo = vi.fn();
    render(
      <WriteCard
        pending={PENDING}
        result={{ id: "l1", status: "written", message: "Link 1 note to [[E]]" }}
        onResult={vi.fn()}
        onUndo={onUndo}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Saved: Link 1 note to [[E]]");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Undone: Link 1 note to [[E]]"));
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/l1/undo", NO_BODY);
    expect(onUndo).toHaveBeenCalledWith({ id: "u1", status: "undone", message: "Undone: Link 1 note to [[E]]" });
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("keeps Undo and shows why when an undo is refused", async () => {
    respond({ id: "l1", status: "refused", message: "notes/a.md changed after this write." });
    const onUndo = vi.fn();
    render(
      <WriteCard pending={PENDING} result={{ id: "l1", status: "written", message: "m" }} onResult={vi.fn()} onUndo={onUndo} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("notes/a.md changed after this write.");
    expect(onUndo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("shows a refused write as a warning without Undo", () => {
    render(
      <WriteCard
        pending={PENDING}
        result={{ id: "p1", status: "refused", message: "notes/a.md changed since it was read." }}
        onResult={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("notes/a.md changed since it was read.");
    expect(screen.getByRole("status")).toHaveClass("warn");
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("shows a failed request and lets you try again", async () => {
    respond({ detail: "This change is no longer waiting for approval. Preview it again." }, false, 409);
    const onResult = vi.fn();
    render(<WriteCard pending={PENDING} onResult={onResult} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This change is no longer waiting for approval. Preview it again.",
    );
    expect(onResult).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Agreement } from "./ClaimsView";
import { DraftEvergreen, EditTopic, LinkNotes } from "./WriteActions";
import { CLAIMS } from "../lib/claimsFixture";

const fetchMock = vi.fn();
const JSON_HEADERS = { "Content-Type": "application/json" };

const PREVIEW = {
  id: "p1",
  kind: "edit_topic",
  summary: "Topic Big: add sleep, remove x",
  files: [{ file: ".kai/topics.yaml", lines: [{ op: "+", text: "    tags: [y, sleep]" }] }],
};

function respond(body: unknown, ok = true, status = 200) {
  fetchMock.mockResolvedValueOnce({ ok, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("EditTopic", () => {
  it("previews removed and added tags, then saves on Approve", async () => {
    respond(PREVIEW);
    respond({ id: "l1", status: "written", message: "Topic Big: add sleep, remove x" });
    const onChange = vi.fn();
    render(<EditTopic topic="big" tags={["x", "y"]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit topic" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove x" }));
    expect(screen.getByRole("button", { name: "Keep x" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByPlaceholderText("sleep, rest"), { target: { value: "#sleep" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview change" }));

    expect(await screen.findByText("Topic Big: add sleep, remove x")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/tags", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ add: ["sleep"], remove: ["x"] }),
    });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Preview change" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Remove x" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByPlaceholderText("sleep, rest")).toHaveValue("");
  });

  it("shows why a change cannot be planned", async () => {
    respond({ detail: "Nothing to change." }, false, 400);
    render(<EditTopic topic="big" tags={["x"]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit topic" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview change" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing to change.");
    expect(screen.queryByTestId("write-card")).toBeNull();
  });

  it("closes the editor", () => {
    render(<EditTopic topic="big" tags={["x"]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit topic" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(screen.queryByTestId("edit-topic")).toBeNull();
  });
});

describe("LinkNotes", () => {
  it("plans links for the unlinked notes and reloads after the write and the undo", async () => {
    respond({ ...PREVIEW, kind: "link_notes", summary: "Link 1 note to [[Ev]]" });
    respond({ id: "l1", status: "written", message: "Link 1 note to [[Ev]]" });
    respond({ id: "u1", status: "undone", message: "Undone: Link 1 note to [[Ev]]" });
    const onChange = vi.fn();
    render(<LinkNotes evergreen="notes/evergreen/ev.md" notes={["notes/b.md"]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Link it to the evergreen" }));

    expect(await screen.findByText("Link 1 note to [[Ev]]")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/links", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ evergreen: "notes/evergreen/ev.md", notes: ["notes/b.md"] }),
    });
    expect(screen.queryByRole("button", { name: "Link it to the evergreen" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2));
  });

  it("says them for several notes", () => {
    render(<LinkNotes evergreen="e.md" notes={["a.md", "b.md"]} onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Link them to the evergreen" })).toBeInTheDocument();
  });
});

describe("Agreement link action", () => {
  it("gets the evergreen and the unlinked note paths", () => {
    const linkAction = vi.fn(() => <span>action</span>);

    render(<Agreement view={CLAIMS} vault="v" linkAction={linkAction} />);

    expect(linkAction).toHaveBeenCalledWith("notes/evergreen/ev.md", ["notes/b.md"]);
    expect(screen.getByText("action")).toBeInTheDocument();
  });

  it("has no action without an evergreen", () => {
    const linkAction = vi.fn(() => <span>action</span>);
    const view = { ...CLAIMS, shared: [{ ...CLAIMS.shared[0], evergreen: null }] };

    render(<Agreement view={view} vault="v" linkAction={linkAction} />);

    expect(linkAction).not.toHaveBeenCalled();
  });
});

const DRAFT_PREVIEW = {
  id: "p9",
  kind: "evergreen",
  summary: "New evergreen: Plans help",
  files: [{ file: "notes/evergreen/plans-help.md", lines: [{ op: "+", text: "# Plans help" }] }],
};

describe("DraftEvergreen", () => {
  it("waits for picked claims", () => {
    render(<DraftEvergreen topic="big" claimIds={[]} onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Draft new evergreen" })).toBeDisabled();
    expect(screen.getByText("Tick claims above to start a draft.")).toBeInTheDocument();
  });

  it("opens the draft for review and saves only on Approve", async () => {
    respond({ body: "## Claims this draws on\n\n- Plans help. ([[a]])\n" });
    respond(DRAFT_PREVIEW);
    respond({ id: "l9", status: "written", message: "New evergreen: Plans help" });
    const onChange = vi.fn();
    render(<DraftEvergreen topic="big" claimIds={["c1", "c3"]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 2 claims" }));

    expect(await screen.findByRole("textbox", { name: "Text" })).toHaveValue(
      "## Claims this draws on\n\n- Plans help. ([[a]])\n",
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/evergreen-draft", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ claim_ids: ["c1", "c3"] }),
    });
    expect(screen.getByRole("button", { name: "Preview save" })).toBeDisabled();

    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Plans help" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Text" }), { target: { value: "Mine." } });
    fireEvent.click(screen.getByRole("button", { name: "Preview save" }));

    expect(await screen.findByText("New evergreen: Plans help")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith("/api/writes/evergreen", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ topic: "big", title: "Plans help", body: "Mine." }),
    });
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenLastCalledWith("/api/writes/p9/apply", {
      method: "POST",
      headers: undefined,
      body: undefined,
    });
  });

  it("offers Approve again on a new preview after an undo", async () => {
    respond({ body: "draft" });
    respond(DRAFT_PREVIEW);
    respond({ id: "l9", status: "written", message: "New evergreen: Plans help" });
    respond({ id: "u9", status: "undone", message: "Undone: New evergreen: Plans help" });
    respond({ ...DRAFT_PREVIEW, id: "p10" });
    render(<DraftEvergreen topic="big" claimIds={["c1"]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Title" }), { target: { value: "T" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview save" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Undone: New evergreen: Plans help");
    fireEvent.click(screen.getByRole("button", { name: "Preview save" }));

    expect(await screen.findByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("Discard closes the draft without a save", async () => {
    respond({ body: "draft" });
    const onChange = vi.fn();
    render(<DraftEvergreen topic="big" claimIds={["c1"]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" }));
    fireEvent.click(await screen.findByRole("button", { name: "Discard" }));

    expect(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("Cancel on the preview forgets the planned note", async () => {
    respond({ body: "draft" });
    respond(DRAFT_PREVIEW);
    respond(undefined, true, 204);
    const onChange = vi.fn();
    render(<DraftEvergreen topic="big" claimIds={["c1"]} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Title" }), { target: { value: "T" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview save" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(await screen.findByText("Cancelled. Nothing was written.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith("/api/writes/p9", {
      method: "DELETE",
      headers: undefined,
      body: undefined,
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows why a draft cannot start", async () => {
    respond({ detail: "Unknown claim: c1. Read the notes again." }, false, 400);
    render(<DraftEvergreen topic="big" claimIds={["c1"]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown claim: c1. Read the notes again.");
  });
});

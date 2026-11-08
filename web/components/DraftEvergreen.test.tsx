import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DraftEvergreen, FIRST_DRAFT, UPDATED } from "./DraftEvergreen";

const fetchMock = vi.fn();
const JSON_HEADERS = { "Content-Type": "application/json" };
const NO_BODY = { method: "POST", headers: undefined, body: undefined };

const SOURCES = "## Claims this draws on\n\n- Plans help. ([[a]])\n";
const FIRST = { title: "Plans beat hope", body: `Plans turn hope into steps.\n\n${SOURCES}` };
const SHORTER = { title: "Plans work", body: `Short.\n\n${SOURCES}` };

const DRAFT_PREVIEW = {
  id: "p9",
  kind: "evergreen",
  summary: "New evergreen: Plans work",
  files: [{ file: "notes/evergreen/plans-work.md", lines: [{ op: "+", text: "# Plans work" }] }],
};

function respond(body: unknown, ok = true, status = 200) {
  fetchMock.mockResolvedValueOnce({ ok, status, json: () => Promise.resolve(body) });
}

function done(draft: { title: string; body: string }) {
  respond({ status: "done", message: null, estimate_usd: null, ...draft });
}

function post(body: unknown) {
  return { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) };
}

function writeCall(request: {
  claim_ids: string[];
  title: string;
  body: string;
  instruction: string | null;
  approved: boolean;
}) {
  return ["/api/topics/big/evergreen-draft/write", post(request)];
}

const turns = () =>
  within(screen.getByRole("list", { name: "Changes so far" }))
    .getAllByRole("listitem")
    .map((li) => li.textContent);

// Opens a draft from c1 and c3 and waits for the AI's first version.
async function openDraft(ids = ["c1", "c3"]) {
  respond({ body: SOURCES });
  done(FIRST);
  const onChange = vi.fn();
  const view = render(<DraftEvergreen topic="big" claimIds={ids} onChange={onChange} />);
  const label = `Draft new evergreen from ${ids.length} claim${ids.length === 1 ? "" : "s"}`;
  fireEvent.click(screen.getByRole("button", { name: label }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(FIRST.title));
  return { onChange, view };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("DraftEvergreen", () => {
  it("waits for picked claims", () => {
    render(<DraftEvergreen topic="big" claimIds={[]} onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Draft new evergreen" })).toBeDisabled();
    expect(screen.getByText("Tick claims above to start a draft.")).toBeInTheDocument();
  });

  it("opens with a first draft the AI wrote from the claims", async () => {
    await openDraft();

    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(FIRST.body);
    expect(fetchMock.mock.calls).toEqual([
      ["/api/topics/big/evergreen-draft", post({ claim_ids: ["c1", "c3"] })],
      writeCall({ claim_ids: ["c1", "c3"], title: "", body: SOURCES, instruction: null, approved: false }),
    ]);
    expect(turns()).toEqual([`${FIRST_DRAFT} ${UPDATED}`]);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the claim list while the first draft is being written", async () => {
    respond({ body: SOURCES });
    let finish: (value: unknown) => void = () => undefined;
    fetchMock.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    render(<DraftEvergreen topic="big" claimIds={["c1"]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" }));

    expect(await screen.findByRole("status")).toHaveTextContent("The AI is working on the draft…");
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(SOURCES);
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    finish({ ok: true, status: 200, json: () => Promise.resolve({ status: "done", message: null, estimate_usd: null, ...FIRST }) });

    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(screen.getByRole("textbox", { name: "Text" })).not.toHaveAttribute("readonly");
  });

  it("revises the draft as it is on screen, hand edits included", async () => {
    await openDraft();
    done(SHORTER);
    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "Plans, edited" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Tell the AI what to change" }), {
      target: { value: "  Make it shorter  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(SHORTER.title));
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(SHORTER.body);
    expect(fetchMock).toHaveBeenLastCalledWith(
      ...writeCall({
        claim_ids: ["c1", "c3"],
        title: "Plans, edited",
        body: FIRST.body,
        instruction: "Make it shorter",
        approved: false,
      }),
    );
    expect(screen.getByRole("textbox", { name: "Tell the AI what to change" })).toHaveValue("");
    expect(turns()).toEqual([`${FIRST_DRAFT} ${UPDATED}`, `Make it shorter ${UPDATED}`]);
  });

  it("sends on Enter but not on Shift+Enter", async () => {
    await openDraft();
    const box = screen.getByRole("textbox", { name: "Tell the AI what to change" });
    fireEvent.change(box, { target: { value: "Shorter" } });

    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    done(SHORTER);
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(SHORTER.title));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("keeps the claims it started from when more are ticked later", async () => {
    const { view, onChange } = await openDraft(["c1"]);
    view.rerender(<DraftEvergreen topic="big" claimIds={["c1", "c2"]} onChange={onChange} />);
    done(SHORTER);
    fireEvent.change(screen.getByRole("textbox", { name: "Tell the AI what to change" }), {
      target: { value: "Shorter" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(SHORTER.title));
    expect(fetchMock).toHaveBeenLastCalledWith(
      ...writeCall({ claim_ids: ["c1"], title: FIRST.title, body: FIRST.body, instruction: "Shorter", approved: false }),
    );
  });

  it("Undo last change steps back one version at a time", async () => {
    await openDraft();
    done(SHORTER);
    fireEvent.change(screen.getByRole("textbox", { name: "Tell the AI what to change" }), {
      target: { value: "Shorter" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(SHORTER.title));

    fireEvent.click(screen.getByRole("button", { name: "Undo last change" }));

    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(FIRST.title);
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(FIRST.body);

    fireEvent.click(screen.getByRole("button", { name: "Undo last change" }));

    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(SOURCES);
    expect(screen.getByRole("button", { name: "Undo last change" })).toBeDisabled();
    expect(turns()).toEqual([
      `${FIRST_DRAFT} ${UPDATED}`,
      `Shorter ${UPDATED}`,
      "Undo last change Back to the version before.",
      "Undo last change Back to the version before.",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("asks before going over a cost limit, then runs on Go ahead", async () => {
    respond({ body: SOURCES });
    respond({
      status: "needs_approval",
      message: "This action may cost up to $0.30, over the $0.25 per-action limit.",
      estimate_usd: 0.3,
      title: "",
      body: SOURCES,
    });
    done(FIRST);
    render(<DraftEvergreen topic="big" claimIds={["c1"]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This action may cost up to $0.30, over the $0.25 per-action limit. Estimated cost: $0.3000. Go ahead",
    );
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(SOURCES);
    expect(screen.queryByRole("list", { name: "Changes so far" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Go ahead" }));

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue(FIRST.title));
    expect(fetchMock).toHaveBeenLastCalledWith(
      ...writeCall({ claim_ids: ["c1"], title: "", body: SOURCES, instruction: null, approved: true }),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the draft and the instruction when the AI fails", async () => {
    await openDraft();
    respond({ status: "failed", message: "The model call failed.", estimate_usd: null, ...FIRST });
    fireEvent.change(screen.getByRole("textbox", { name: "Tell the AI what to change" }), {
      target: { value: "Shorter" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The model call failed.");
    expect(screen.queryByRole("button", { name: "Go ahead" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(FIRST.body);
    expect(screen.getByRole("textbox", { name: "Tell the AI what to change" })).toHaveValue("Shorter");
    expect(turns()).toEqual([`${FIRST_DRAFT} ${UPDATED}`]);
  });

  it("shows a request error and keeps the draft", async () => {
    await openDraft();
    respond({ detail: "Unknown claim: c1. Read the notes again." }, false, 400);
    fireEvent.change(screen.getByRole("textbox", { name: "Tell the AI what to change" }), {
      target: { value: "Shorter" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown claim: c1. Read the notes again.");
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveValue(FIRST.body);
  });

  it("saves only on Approve, with the draft as it is on screen", async () => {
    const { onChange } = await openDraft();
    respond(DRAFT_PREVIEW);
    respond({ id: "l9", status: "written", message: "New evergreen: Plans work" });
    fireEvent.change(screen.getByRole("textbox", { name: "Text" }), { target: { value: "Mine." } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("New evergreen: Plans work")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/writes/evergreen",
      post({ topic: "big", title: FIRST.title, body: "Mine." }),
    );
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenLastCalledWith("/api/writes/p9/apply", NO_BODY);
  });

  it("needs a title to save", async () => {
    await openDraft();

    fireEvent.change(screen.getByRole("textbox", { name: "Title" }), { target: { value: "  " } });

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("offers Approve again on a new save after an undo", async () => {
    await openDraft(["c1"]);
    respond(DRAFT_PREVIEW);
    respond({ id: "l9", status: "written", message: "New evergreen: Plans work" });
    respond({ id: "u9", status: "undone", message: "Undone: New evergreen: Plans work" });
    respond({ ...DRAFT_PREVIEW, id: "p10" });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(screen.getByText("Undone: New evergreen: Plans work")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("Discard closes the draft without a save", async () => {
    const { onChange } = await openDraft(["c1"]);

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("Cancel on the preview forgets the planned note", async () => {
    const { onChange } = await openDraft(["c1"]);
    respond(DRAFT_PREVIEW);
    respond(undefined, true, 204);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(await screen.findByText("Cancelled. Nothing was written.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith("/api/writes/p9", {
      method: "DELETE",
      headers: undefined,
      body: undefined,
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows why a draft cannot start and asks the AI nothing", async () => {
    respond({ detail: "Unknown claim: c1. Read the notes again." }, false, 400);
    render(<DraftEvergreen topic="big" claimIds={["c1"]} onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unknown claim: c1. Read the notes again.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" })).toBeEnabled();
  });
});

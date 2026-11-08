import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CLAIMS, suggested } from "../lib/claimsFixture";
import type { ClaimsRun, ClaimsView } from "../lib/claims";
import { ClaimsSection, elapsed, splitCounts } from "./ClaimsSection";

const fetchMock = vi.fn();

type Init = { method?: string; body?: string };

/** Serves `view` for GET; `run` customises what POST /run returns. PUT echoes the question back. */
function api(view: ClaimsView, run?: Partial<ClaimsRun>) {
  fetchMock.mockImplementation(async (url: string, init?: Init) => {
    if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
    if (url === "/api/topics/big/claims/run") {
      const result = { status: "done", message: null, estimate_usd: null, view, ...run };
      return { ok: true, json: async () => result };
    }
    if (url === "/api/topics/big/claims/question") {
      const question = JSON.parse(init?.body ?? "{}").question;
      return { ok: true, json: async () => ({ ...view, question, next_step: "sort" }) };
    }
    if (url === "/api/topics/big/claims") return { ok: true, json: async () => view };
    return { ok: true, status: 204 };
  });
}

const FRESH: ClaimsView = {
  ...CLAIMS,
  read_notes: 0,
  pending_notes: 3,
  claim_count: 0,
  questions: [],
  question: null,
  supporting: [],
  pushing_back: [],
  summary: null,
  shared: [],
  stale: true,
  next_step: "read",
};

const READ: ClaimsView = {
  ...FRESH,
  read_notes: 3,
  pending_notes: 0,
  claim_count: 3,
  next_step: "analyse",
};

const ASKED: ClaimsView = {
  ...READ,
  questions: [suggested("Do agents need plans?"), suggested("Do tools matter?")],
  next_step: "done",
};

const CHOSEN: ClaimsView = { ...ASKED, question: "Do agents need plans?", next_step: "sort" };

async function step(name: string) {
  return within(await screen.findByRole("region", { name }));
}

function states() {
  return ["Read your notes", "Choose a question", "How the notes split", "Where they agree"].map(
    (name) => screen.getByRole("region", { name }).getAttribute("data-state"),
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("ClaimsSection layout", () => {
  it("shows four numbered steps in order, each with its own badge", async () => {
    api(FRESH);

    render(<ClaimsSection topic="big" />);
    await screen.findByText("Read 0 of 3 notes, 0 claims.");

    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "1Read your notesDo this next",
      "2Choose a questionWaiting",
      "3How the notes splitWaiting",
      "4Where they agreeWaiting",
    ]);
  });

  it("shows only one action button, inside the step it belongs to", async () => {
    api(FRESH);

    render(<ClaimsSection topic="big" />);

    expect(await (await step("Read your notes")).findByRole("button", { name: "Read notes" })).toBeEnabled();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("says why a waiting step is waiting", async () => {
    api(FRESH);

    render(<ClaimsSection topic="big" />);
    await screen.findByText("Read 0 of 3 notes, 0 claims.");

    expect((await step("Choose a question")).getByText(/Read your notes first/)).toBeInTheDocument();
    expect((await step("How the notes split")).getByText(/Choose a question in step 2/)).toBeInTheDocument();
    expect((await step("Where they agree")).getByText(/Needs step 2 first/)).toBeInTheDocument();
  });
});

describe("ClaimsSection steps", () => {
  it("step 1 is next before anything is read", async () => {
    api(FRESH);
    render(<ClaimsSection topic="big" />);
    await screen.findByText("Read 0 of 3 notes, 0 claims.");

    expect(states()).toEqual(["next", "waiting", "waiting", "waiting"]);
  });

  it("step 2 holds the button that suggests questions once notes are read", async () => {
    api(READ);
    render(<ClaimsSection topic="big" />);

    expect(
      await (await step("Choose a question")).findByRole("button", { name: "Suggest questions and find agreement" }),
    ).toBeEnabled();
    expect(states()).toEqual(["done", "next", "waiting", "waiting"]);
    expect((await step("Choose a question")).getByText("No suggestions yet.")).toBeInTheDocument();
  });

  it("asks the user to pick a question when suggestions exist", async () => {
    api(ASKED);
    render(<ClaimsSection topic="big" />);

    expect(await (await step("Choose a question")).findByText("Pick the yes or no question to sort your claims by.")).toBeInTheDocument();
    expect(states()).toEqual(["done", "next", "waiting", "done"]);
    expect(screen.queryByRole("button", { name: /Suggest|Read|Sort/ })).toBeNull();
  });

  it("leads with the chosen question and its sort button until it is sorted", async () => {
    api(CHOSEN);
    render(<ClaimsSection topic="big" />);

    const answer = within(await screen.findByTestId("answer"));
    expect(answer.getByRole("heading", { level: 2 }).textContent).toBe("Do agents need plans?");
    expect(answer.getByTestId("answer-counts").textContent).toBe("Not sorted yet: 3 claims to place. Change question");
    expect(answer.getByRole("button", { name: "Sort and summarise" })).toBeEnabled();
    expect(screen.queryByTestId("claims-table")).toBeNull();
    expect(screen.queryByTestId("split-summary")).toBeNull();
  });

  it("does not claim the claims are sorted before the sort has run", async () => {
    api(CHOSEN);
    render(<ClaimsSection topic="big" />);

    const done = await screen.findByRole("list", { name: "Finished steps" });
    expect(within(done).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "✓ 3 notes read · 3 claims",
      "✓ Question chosen",
    ]);
  });

  it("leads with the answer and folds finished steps into one line each", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);

    const answer = within(await screen.findByTestId("answer"));
    expect(answer.getByRole("heading", { level: 2 }).textContent).toBe("Do agents need plans?");
    expect(answer.getByTestId("answer-counts").textContent).toBe(
      "1 claim for (from 1 note), 1 against (from 1 note) Change question",
    );
    expect(answer.getByTestId("split-summary")).toBeInTheDocument();
    expect(answer.getByTestId("claims-table").closest("details")?.open).toBe(false);
    expect(answer.getByRole("button", { name: "Draft evergreen from this answer" })).toBeEnabled();
    const done = screen.getByRole("list", { name: "Finished steps" });
    expect(within(done).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "✓ 3 notes read · 3 claims",
      "✓ Question chosen",
      "✓ Sorted and summarised",
    ]);
    expect(screen.queryByRole("region", { name: "Read your notes" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Choose a question" })).toBeNull();
    expect(screen.getByTestId("agreement")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Read|Suggest|Sort/ })).toBeNull();
    expect(screen.queryByTestId("questions")).toBeNull();
  });

  it("leads with the question when notes changed and the analysis must run again", async () => {
    api({ ...CHOSEN, next_step: "analyse" });
    render(<ClaimsSection topic="big" />);

    const answer = within(await screen.findByTestId("answer"));
    expect(answer.getByRole("heading", { level: 2 }).textContent).toBe("Do agents need plans?");
    expect(answer.getByTestId("answer-counts").textContent).toBe(
      "Your notes changed since the last analysis. Change question",
    );
    expect(answer.getByRole("button", { name: "Sort and summarise" })).toBeEnabled();
  });

  it("runs the open steps when a question is chosen while the analysis is out of date", async () => {
    api({ ...ASKED, next_step: "analyse" }, { view: CLAIMS });
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[0]);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: false }),
      }),
    );
    expect(await screen.findByTestId("split-summary")).toBeInTheDocument();
  });

  it("asks for approval right under the question, not in the step list", async () => {
    api({ ...CHOSEN, next_step: "analyse" }, {
      status: "needs_approval",
      message: "Over the limit.",
      estimate_usd: 0.01,
      view: { ...CHOSEN, next_step: "analyse" },
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Sort and summarise" }));

    const work = within(await screen.findByTestId("answer-work"));
    expect((await work.findByRole("status")).textContent).toBe("Over the limit. Estimated next call: $0.0100.Run anyway");
    expect(work.getByRole("button", { name: "Run anyway" })).toBeEnabled();
  });

  it("shows how long the run has been working", async () => {
    let finish: (value: unknown) => void = () => {};
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
      if (url === "/api/topics/big/claims/run") {
        await new Promise((resolve) => (finish = resolve));
        return { ok: true, json: async () => ({ status: "done", message: null, estimate_usd: null, view: CLAIMS }) };
      }
      return { ok: true, json: async () => CHOSEN };
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Sort and summarise" }));

    expect(await screen.findByText(/^ ?Working on your question: 0:0\d\. A big topic can take a few minutes\.$/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
    finish(null);
    expect(await screen.findByTestId("split-summary")).toBeInTheDocument();
    expect(screen.queryByText(/Working on your question/)).toBeNull();
  });

  it("opens and closes the question picker from the answer", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);
    const toggle = await screen.findByRole("button", { name: "Change question" });

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const picker = within(screen.getByRole("region", { name: "Change question" }));
    expect(picker.getAllByRole("listitem", { name: "" }).length).toBeGreaterThan(0);
    expect(picker.getByRole("textbox", { name: "Your own question" })).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByRole("region", { name: "Change question" })).toBeNull();
  });

  it("closes the picker and sorts the new question in one click", async () => {
    api(CLAIMS, { view: CLAIMS });
    render(<ClaimsSection topic="big" />);
    fireEvent.click(await screen.findByRole("button", { name: "Change question" }));

    fireEvent.click(screen.getByRole("button", { name: "Use this question" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: false }),
      }),
    );
    expect(screen.queryByRole("region", { name: "Change question" })).toBeNull();
  });

  it("tells the page when it leads with the answer", async () => {
    const onAnswerFirst = vi.fn();
    api(READ);
    const { unmount } = render(<ClaimsSection topic="big" onAnswerFirst={onAnswerFirst} />);
    await screen.findByRole("region", { name: "Read your notes" });
    expect(onAnswerFirst).toHaveBeenLastCalledWith(false);
    unmount();

    api(CLAIMS);
    render(<ClaimsSection topic="big" onAnswerFirst={onAnswerFirst} />);
    await screen.findByTestId("answer");
    expect(onAnswerFirst).toHaveBeenLastCalledWith(true);
  });

  it("offers Read more notes when notes were added after the first read", async () => {
    api({ ...CLAIMS, pending_notes: 2, next_step: "read" });
    render(<ClaimsSection topic="big" />);

    expect(await (await step("Read your notes")).findByRole("button", { name: "Read more notes" })).toBeEnabled();
    expect(screen.getByText("Read 3 of 5 notes, 3 claims. Run again to read the rest.")).toBeInTheDocument();
  });
});

describe("ClaimsSection actions", () => {
  it("posts the run and shows what came back", async () => {
    api(FRESH, { view: CLAIMS });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Read notes" }));

    expect(await screen.findByTestId("claims-table")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved: false }),
    });
  });

  it("disables the buttons and says Working while a run is in progress", async () => {
    let finish: (value: unknown) => void = () => undefined;
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/topics/big/claims/run") return new Promise((resolve) => (finish = resolve));
      if (url === "/api/ai/status") return Promise.resolve({ ok: true, json: async () => ({ vault_name: "v" }) });
      return Promise.resolve({ ok: true, json: async () => READ });
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Suggest questions and find agreement" }));

    expect(await screen.findByRole("button", { name: "Working…" })).toBeDisabled();
    expect(screen.getByText("This can take a minute.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use my question" })).toBeDisabled();
    finish({ ok: true, json: async () => ({ status: "done", message: null, estimate_usd: null, view: CLAIMS }) });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Working…" })).toBeNull());
    expect(screen.queryByText("This can take a minute.")).toBeNull();
  });

  it("asks before going over the cost limit, under the button, and runs again when approved", async () => {
    api(FRESH, { status: "needs_approval", message: "Over the limit.", estimate_usd: 0.1234 });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Read notes" }));

    const notice = await (await step("Read your notes")).findByRole("status");
    expect(notice).toHaveTextContent("Over the limit. Estimated next call: $0.1234.");
    fireEvent.click(within(notice).getByRole("button", { name: "Run anyway" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true }),
      }),
    );
  });

  it("shows a failed run as an alert in the step that failed", async () => {
    api(READ, { status: "failed", message: "The model call failed." });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Suggest questions and find agreement" }));

    expect(await (await step("Choose a question")).findByRole("alert")).toHaveTextContent("The model call failed.");
  });

  it("clears an earlier notice when a new run starts", async () => {
    api(FRESH, { status: "failed", message: "The model call failed." });
    render(<ClaimsSection topic="big" />);
    fireEvent.click(await screen.findByRole("button", { name: "Read notes" }));
    await screen.findByRole("alert");

    api(FRESH, { view: CLAIMS });
    fireEvent.click(screen.getByRole("button", { name: "Read notes" }));

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("one click saves the question, then sorts and summarises it", async () => {
    api(ASKED, { view: CLAIMS });
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[1]);

    expect(await screen.findByTestId("split-summary")).toBeInTheDocument();
    const calls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/topics/big/claims/"));
    expect(calls).toEqual([
      [
        "/api/topics/big/claims/question",
        { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: "Do tools matter?" }) },
      ],
      [
        "/api/topics/big/claims/run",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved: false }) },
      ],
    ]);
  });

  it("asks once for approval when the one-click run would cost too much", async () => {
    api(ASKED, { status: "needs_approval", message: "Over the limit.", estimate_usd: 0.3, view: CHOSEN });
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[0]);

    expect(await screen.findByRole("button", { name: "Run anyway" })).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "Run anyway" })).toHaveLength(1);
    expect(screen.getByText("Over the limit. Estimated next call: $0.3000.")).toBeInTheDocument();
    const runs = fetchMock.mock.calls.filter(([url]) => url === "/api/topics/big/claims/run");
    expect(runs).toHaveLength(1);
  });

  it("shows the summary first and keeps the claim lists folded under it", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);

    const summary = await screen.findByTestId("split-summary");
    expect(within(summary).getByText("The notes lean yes.")).toBeInTheDocument();
    expect(within(within(summary).getByRole("list", { name: "Where they agree" })).getByRole("listitem").textContent).toBe("Plans matter.");
    expect(within(within(summary).getByRole("list", { name: "Where they split" })).getByRole("listitem").textContent).toBe(
      "Yes rests on studies; no on experience.",
    );
    expect(within(within(summary).getByRole("list", { name: "What is missing" })).getByRole("listitem").textContent).toBe(
      "A direct comparison.",
    );
    expect(within(summary).getByRole("link", { name: "A" })).toHaveAttribute("href", "obsidian://open?vault=v&file=notes%2Fa");
    const folded = screen.getByText("All claims for and against (2)").closest("details");
    expect(folded).not.toBeNull();
    expect(folded?.open).toBe(false);
    expect(within(folded as HTMLElement).getByTestId("claims-table")).toBeInTheDocument();
  });

  it("does not run when saving the question fails", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
      if (url === "/api/topics/big/claims/question") {
        return { ok: false, status: 422, json: async () => ({ detail: "Too long" }) };
      }
      return { ok: true, json: async () => ASKED };
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[0]);

    expect(await screen.findByText("Too long")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/topics/big/claims/run", expect.anything());
  });

  it("saves an edited question, trimmed", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);
    fireEvent.click(await screen.findByRole("button", { name: "Change question" }));
    const input = screen.getByRole("textbox", { name: "Your own question" });

    fireEvent.change(input, { target: { value: "  Why plan?  " } });
    fireEvent.click(screen.getByRole("button", { name: "Use my question" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/question", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: "Why plan?" }),
      }),
    );
  });

  it("does not save a blank question when none is chosen", async () => {
    api(ASKED);
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Use my question" }));

    expect(fetchMock).not.toHaveBeenCalledWith("/api/topics/big/claims/question", expect.anything());
  });

  it("marks the chosen question in the picker", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);
    fireEvent.click(await screen.findByRole("button", { name: "Change question" }));

    expect(screen.getByRole("button", { name: "Chosen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use this question" })).toBeInTheDocument();
  });

  it("shows each question's kind, why line and the notes behind it", async () => {
    api({ ...ASKED, questions: [suggested("Do plans help?")] });
    render(<ClaimsSection topic="big" />);

    const item = within(await screen.findByTestId("questions")).getAllByRole("listitem")[0];
    expect(within(item).getByText("Notes disagree:")).toBeInTheDocument();
    expect(within(item).getByText("Why: Do plans help?")).toBeInTheDocument();
    const anchors = within(item).getByRole("list", { name: "Claims behind: Do plans help?" });
    expect(within(anchors).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Plans help. A",
      "Plans fail. K",
    ]);
    expect(within(anchors).getByRole("link", { name: "A" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=v&file=notes%2Fa",
    );
    expect(within(item).getByText("From 2 claims")).toBeInTheDocument();
  });

  it("offers only disagreement questions for sorting, not recurring or carry-over ones", async () => {
    api({
      ...ASKED,
      questions: [
        suggested("Do plans help?"),
        suggested("What is judgment?", "recurring"),
        suggested("Does explicit state carry over?", "carry_over"),
      ],
    });
    render(<ClaimsSection topic="big" />);

    const list = await screen.findByTestId("questions");
    expect(within(list).getAllByRole("button", { name: "Use this question" })).toHaveLength(1);
    expect(within(list).getByText("Keeps coming back:")).toBeInTheDocument();
    expect(within(list).getByText("Does it carry over?:")).toBeInTheDocument();
  });

  it("says no question stood out when the suggestions came back empty", async () => {
    api({ ...ASKED, questions: [] });
    render(<ClaimsSection topic="big" />);

    expect(
      await (await step("Choose a question")).findByText(
        "No question stood out in these notes. You can still write your own.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("questions")).toBeNull();
  });

  it("shows an error and keeps the old view when saving the question fails", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
      if (url === "/api/topics/big/claims/question") {
        return { ok: false, status: 422, json: async () => ({ detail: "Too long" }) };
      }
      return { ok: true, json: async () => ASKED };
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[0]);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/question", expect.anything()));
    expect(screen.getAllByRole("button", { name: "Use this question" })).toHaveLength(2);
  });

  it("reloads when the topic changes", async () => {
    api(CLAIMS);
    const { rerender } = render(<ClaimsSection topic="big" />);
    await screen.findByTestId("claims-table");

    rerender(<ClaimsSection topic="other" />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/topics/other/claims"));
  });

  it("shows a load error instead of the steps", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url === "/api/topics/big/claims"
        ? { ok: false, status: 404, json: async () => ({ detail: "No topic data yet." }) }
        : { ok: false, status: 500, json: async () => ({}) },
    );

    render(<ClaimsSection topic="big" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("No topic data yet.");
    expect(screen.queryByRole("region", { name: "Read your notes" })).toBeNull();
  });
});

describe("ClaimsSection link notes", () => {
  it("links the unlinked notes and reads the claims again after the write", async () => {
    const preview = { id: "p1", kind: "link_notes", summary: "Link 1 note to [[Ev]]", files: [] };
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
      if (url === "/api/topics/big/claims") return { ok: true, json: async () => CLAIMS };
      if (url === "/api/writes/links") return { ok: true, status: 200, json: async () => preview };
      if (url === "/api/writes/p1/apply") {
        return { ok: true, status: 200, json: async () => ({ id: "l1", status: "written", message: "ok" }) };
      }
      return { ok: true, status: 204 };
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Link it to the evergreen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));

    const claimReads = () => fetchMock.mock.calls.filter(([url]) => url === "/api/topics/big/claims").length;
    await waitFor(() => expect(claimReads()).toBe(2));
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/links", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ evergreen: "notes/evergreen/ev.md", notes: ["notes/b.md"] }),
    });
  });
});

describe("ClaimsSection draft new evergreen", () => {
  it("drafts from the ticked claims, then clears them and re-reads after the save", async () => {
    const preview = { id: "p9", kind: "evergreen", summary: "New evergreen: T", files: [] };
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
      if (url === "/api/topics/big/claims") return { ok: true, json: async () => CLAIMS };
      if (url === "/api/topics/big/evergreen-draft") return { ok: true, json: async () => ({ body: "draft" }) };
      if (url === "/api/topics/big/evergreen-draft/write") {
        const first = { status: "done", message: null, estimate_usd: null, title: "AI title", body: "AI text" };
        return { ok: true, status: 200, json: async () => first };
      }
      if (url === "/api/writes/evergreen") return { ok: true, status: 200, json: async () => preview };
      if (url === "/api/writes/p9/apply") {
        return { ok: true, status: 200, json: async () => ({ id: "l9", status: "written", message: "ok" }) };
      }
      return { ok: true, status: 204 };
    });
    const onWrite = vi.fn();
    render(<ClaimsSection topic="big" onWrite={onWrite} />);

    expect(await screen.findByRole("button", { name: "Draft new evergreen" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Pick claim: Plans help." }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pick claim: Agents need tools." }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pick claim: Plans help." }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pick claim: Plans help." }));
    fireEvent.click(screen.getByRole("button", { name: "Draft new evergreen from 3 claims" }));

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("AI title"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => expect(onWrite).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith("/api/writes/evergreen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic: "big", title: "AI title", body: "AI text" }),
    });
    const claimReads = () => fetchMock.mock.calls.filter(([url]) => url === "/api/topics/big/claims").length;
    await waitFor(() => expect(claimReads()).toBe(2));
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/evergreen-draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim_ids: ["c2", "c3", "c1"] }),
    });
    expect(screen.getAllByRole("checkbox").filter((c) => (c as HTMLInputElement).checked)).toEqual([]);
  });

  it("drops ticks for claims no longer on screen", async () => {
    api(CLAIMS);
    const { rerender } = render(<ClaimsSection topic="big" />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Pick claim: Plans help." }));
    expect(screen.getByRole("button", { name: "Draft new evergreen from 1 claim" })).toBeEnabled();

    const other = { ...CLAIMS, topic: "other", supporting: [] };
    fetchMock.mockImplementation(async (url: string) =>
      url === "/api/topics/other/claims"
        ? { ok: true, json: async () => other }
        : { ok: true, json: async () => ({ vault_name: "v" }) },
    );
    rerender(<ClaimsSection topic="other" />);

    expect(await screen.findByRole("button", { name: "Draft new evergreen" })).toBeDisabled();
  });

  it("hides the draft step until claims are sorted or matched", async () => {
    api(READ);
    render(<ClaimsSection topic="big" />);

    await step("Read your notes");
    expect(screen.queryByRole("region", { name: "Draft new evergreen" })).toBeNull();
  });
});

describe("splitCounts", () => {
  const claim = (id: string, path: string) => ({ id, text: id, path, title: path });
  it("counts claims and distinct notes per side, with plurals and thousands", () => {
    const many = Array.from({ length: 1200 }, (_, i) => claim(`s${i}`, `n${i % 2}.md`));
    expect(splitCounts({ ...CLAIMS, supporting: many, pushing_back: [claim("p", "x.md")] })).toBe(
      "1,200 claims for (from 2 notes), 1 against (from 1 note)",
    );
    expect(splitCounts({ ...CLAIMS, supporting: [], pushing_back: [] })).toBe(
      "0 claims for (from 0 notes), 0 against (from 0 notes)",
    );
  });
});

describe("elapsed", () => {
  it("formats minutes and zero-padded seconds", () => {
    expect(elapsed(0)).toBe("0:00");
    expect(elapsed(9_999)).toBe("0:09");
    expect(elapsed(65_000)).toBe("1:05");
    expect(elapsed(600_000)).toBe("10:00");
    expect(elapsed(-5)).toBe("0:00");
  });
});

describe("ClaimsSection draft from the answer", () => {
  it("sends both sides of the answer to the draft, no ticking needed", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
      if (url === "/api/topics/big/claims") return { ok: true, json: async () => CLAIMS };
      if (url === "/api/topics/big/evergreen-draft") return { ok: true, json: async () => ({ body: "draft" }) };
      return {
        ok: true,
        status: 200,
        json: async () => ({ status: "done", message: null, estimate_usd: null, title: "Plans help", body: "B" }),
      };
    });
    render(<ClaimsSection topic="big" />);

    const answer = within(await screen.findByTestId("answer"));
    fireEvent.click(answer.getByRole("button", { name: "Draft evergreen from this answer" }));

    await waitFor(() => expect(answer.getByRole("textbox", { name: "Title" })).toHaveValue("Plans help"));
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/evergreen-draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim_ids: ["c1", "c2"] }),
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/evergreen-draft/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim_ids: ["c1", "c2"], title: "", body: "draft", instruction: null, answer: true, approved: false }),
    });
  });
});

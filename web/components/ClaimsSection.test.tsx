import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CLAIMS } from "../lib/claimsFixture";
import type { ClaimsRun, ClaimsView } from "../lib/claims";
import { ClaimsSection } from "./ClaimsSection";

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
  questions: ["Do agents need plans?", "Do tools matter?"],
  next_step: "done",
};

const CHOSEN: ClaimsView = { ...ASKED, question: "Do agents need plans?", next_step: "sort" };

async function step(name: string) {
  return within(await screen.findByRole("region", { name }));
}

function states() {
  return ["Read your notes", "Choose a question", "Claims for and against", "Where they agree"].map(
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
      "3Claims for and againstWaiting",
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
    expect((await step("Claims for and against")).getByText(/Choose a question in step 2/)).toBeInTheDocument();
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

  it("step 3 holds the sort button once a question is chosen", async () => {
    api(CHOSEN);
    render(<ClaimsSection topic="big" />);

    const button = await (await step("Claims for and against")).findByRole("button", {
      name: "Sort claims by this question",
    });
    expect(button).toBeEnabled();
    expect(states()).toEqual(["done", "done", "next", "done"]);
    expect((await step("Claims for and against")).getByText(/Do agents need plans\?.*place the 3 claims on a side/)).toBeInTheDocument();
    expect(screen.queryByTestId("claims-table")).toBeNull();
  });

  it("does not claim the claims are sorted before the sort has run", async () => {
    api(CHOSEN);
    render(<ClaimsSection topic="big" />);

    expect(await (await step("Choose a question")).findByText("Question chosen. You can switch to another at any time.")).toBeInTheDocument();
    expect(screen.queryByText(/are sorted/)).toBeNull();
  });

  it("shows the table and the agreement when everything is done, with no button", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);

    expect(await screen.findByTestId("claims-table")).toBeInTheDocument();
    expect(screen.getByTestId("agreement")).toBeInTheDocument();
    expect(states()).toEqual(["done", "done", "done", "done"]);
    expect(screen.queryByRole("button", { name: /Read|Suggest|Sort/ })).toBeNull();
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

  it("saves a proposed question and then offers the sort", async () => {
    api(ASKED);
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[1]);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/question", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: "Do tools matter?" }),
      }),
    );
    expect(await (await step("Claims for and against")).findByRole("button", { name: "Sort claims by this question" })).toBeEnabled();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/topics/big/claims/run", expect.anything());
  });

  it("saves an edited question, trimmed", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);
    const input = await screen.findByRole("textbox", { name: "Your own question" });

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

  it("marks the chosen question", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);

    expect(await screen.findByRole("button", { name: "Chosen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use this question" })).toBeInTheDocument();
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

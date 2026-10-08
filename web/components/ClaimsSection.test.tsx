import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CLAIMS } from "../lib/claimsFixture";
import type { ClaimsRun, ClaimsView } from "../lib/claims";
import { ClaimsSection } from "./ClaimsSection";

const fetchMock = vi.fn();

function api(view: ClaimsView, run?: Partial<ClaimsRun>) {
  fetchMock.mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
    if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
    if (url === "/api/topics/big/claims/run") {
      const result = { status: "done", message: null, estimate_usd: null, view, ...run };
      return { ok: true, json: async () => result };
    }
    if (url === "/api/topics/big/claims/question") {
      const question = JSON.parse(init?.body ?? "{}").question;
      return { ok: true, json: async () => ({ ...view, question }) };
    }
    if (url === "/api/topics/big/claims") return { ok: true, json: async () => view };
    return { ok: true, status: 204 };
  });
}

const EMPTY: ClaimsView = {
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
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("ClaimsSection", () => {
  it("offers Find claims before anything is read, and no table", async () => {
    api(EMPTY);

    render(<ClaimsSection topic="big" />);

    expect(await screen.findByText("Read 0 of 3 notes, 0 claims.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Find claims" })).toBeEnabled();
    expect(screen.queryByTestId("claims-table")).toBeNull();
  });

  it("runs, then shows the claims and the agreement without a second button", async () => {
    api(CLAIMS);
    render(<ClaimsSection topic="big" />);
    expect(await screen.findByText("Read 3 of 3 notes, 3 claims.")).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: /claims$/ })).toBeNull();
    expect(screen.getByTestId("claims-table")).toBeInTheDocument();
    expect(screen.getByTestId("agreement")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims");
  });

  it("posts the run and shows what came back", async () => {
    api(EMPTY, { view: CLAIMS });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Find claims" }));

    expect(await screen.findByTestId("claims-table")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved: false }),
    });
  });

  it("asks before going over the cost limit and runs again when approved", async () => {
    api(EMPTY, { status: "needs_approval", message: "Over the limit.", estimate_usd: 0.1234 });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Find claims" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Over the limit. Estimated next call: $0.1234.");
    fireEvent.click(screen.getByRole("button", { name: "Run anyway" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true }),
      }),
    );
  });

  it("shows a failed run as an alert", async () => {
    api(EMPTY, { status: "failed", message: "The model call failed." });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Find claims" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The model call failed.");
  });

  it("offers Update claims when notes changed", async () => {
    api({ ...CLAIMS, stale: true, pending_notes: 1 });

    render(<ClaimsSection topic="big" />);

    expect(await screen.findByRole("button", { name: "Update claims" })).toBeEnabled();
    expect(screen.getByText("Read 3 of 4 notes, 3 claims. Run again to read the rest.")).toBeInTheDocument();
  });

  it("saves a proposed question, then runs", async () => {
    api({ ...CLAIMS, question: null, supporting: [], pushing_back: [] });
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[1]);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/question", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: "Do tools matter?" }),
      }),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/topics/big/claims/run", expect.anything()),
    );
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

  it("marks the chosen question", async () => {
    api(CLAIMS);

    render(<ClaimsSection topic="big" />);

    expect(await screen.findByRole("button", { name: "Chosen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use this question" })).toBeInTheDocument();
  });

  it("disables the buttons and says Working while a run is in progress", async () => {
    let finish: (value: unknown) => void = () => undefined;
    fetchMock.mockImplementation((url: string) => {
      if (url === "/api/topics/big/claims/run") return new Promise((resolve) => (finish = resolve));
      if (url === "/api/ai/status") return Promise.resolve({ ok: true, json: async () => ({ vault_name: "v" }) });
      return Promise.resolve({ ok: true, json: async () => ({ ...CLAIMS, stale: true }) });
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Update claims" }));

    expect(await screen.findByRole("button", { name: "Working…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use my question" })).toBeDisabled();
    finish({ ok: true, json: async () => ({ status: "done", message: null, estimate_usd: null, view: CLAIMS }) });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Working…" })).toBeNull());
  });

  it("clears an earlier notice when a new run starts", async () => {
    api({ ...EMPTY }, { status: "failed", message: "The model call failed." });
    render(<ClaimsSection topic="big" />);
    fireEvent.click(await screen.findByRole("button", { name: "Find claims" }));
    await screen.findByRole("alert");

    api({ ...EMPTY }, { view: CLAIMS });
    fireEvent.click(screen.getByRole("button", { name: "Find claims" }));

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("shows an error and does not run when saving the question fails", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === "/api/ai/status") return { ok: true, json: async () => ({ vault_name: "v" }) };
      if (url === "/api/topics/big/claims/question") {
        return { ok: false, status: 422, json: async () => ({ detail: "Too long" }) };
      }
      return { ok: true, json: async () => ({ ...CLAIMS, question: null }) };
    });
    render(<ClaimsSection topic="big" />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Use this question" }))[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent("Too long");
    expect(fetchMock).not.toHaveBeenCalledWith("/api/topics/big/claims/run", expect.anything());
  });

  it("does not save a blank custom question when none is chosen", async () => {
    api({ ...CLAIMS, question: null });
    render(<ClaimsSection topic="big" />);

    fireEvent.click(await screen.findByRole("button", { name: "Use my question" }));

    expect(fetchMock).not.toHaveBeenCalledWith("/api/topics/big/claims/question", expect.anything());
  });

  it("reloads when the topic changes", async () => {
    api(CLAIMS);
    const { rerender } = render(<ClaimsSection topic="big" />);
    await screen.findByTestId("claims-table");

    rerender(<ClaimsSection topic="other" />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/topics/other/claims"));
  });

  it("shows a load error", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url === "/api/topics/big/claims"
        ? { ok: false, status: 404, json: async () => ({ detail: "No topic data yet." }) }
        : { ok: false, status: 500, json: async () => ({}) },
    );

    render(<ClaimsSection topic="big" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("No topic data yet.");
  });
});

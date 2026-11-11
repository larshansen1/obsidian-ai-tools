import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BriefingsScreen } from "./BriefingsScreen";
import type { BriefingList, BriefingRow } from "../lib/briefings";
import { job } from "../lib/ingestFixture";
import { formatWhen } from "../lib/writes";

const fetchMock = vi.fn();
const JSON_HEADERS = { "Content-Type": "application/json" };

function row(id: string, fields: Partial<BriefingRow> = {}): BriefingRow {
  return {
    id,
    url: `https://example.com/${id}`,
    title: `Title ${id}`,
    site: "example.com",
    date: "2026-11-02",
    section: "AI news",
    times_seen: 3,
    ...fields,
  };
}

const LIST: BriefingList = {
  groups: [
    { topic_id: "ai", topic_name: "AI", links: [row("a"), row("b", { section: null, times_seen: 1 })] },
    { topic_id: null, topic_name: null, links: [row("c")] },
  ],
  added: [],
  pending_checks: 0,
  last_pull: "2026-11-10T12:00:00Z",
};

type Reply = { ok: boolean; status: number; json: () => Promise<unknown> };
const reply = (body: unknown, ok = true, status = 200): Reply => ({ ok, status, json: () => Promise.resolve(body) });

let routes: Record<string, Reply[]> = {};

function on(url: string, ...replies: Reply[]) {
  routes[url] = [...(routes[url] ?? []), ...replies];
}

const AI_STATUS = { vault_name: "My Vault" };

beforeEach(() => {
  routes = {};
  fetchMock.mockReset();
  fetchMock.mockImplementation((url: string) => {
    if (url === "/api/usage") return Promise.resolve({ ok: true, status: 204 });
    const next = routes[url]?.shift();
    return Promise.resolve(next ?? reply({ detail: `unexpected ${url}` }, false, 500));
  });
  vi.stubGlobal("fetch", fetchMock);
  on("/api/ai/status", reply(AI_STATUS), reply(AI_STATUS));
});

const usage = () =>
  fetchMock.mock.calls
    .filter(([url]) => url === "/api/usage")
    .map(([, init]) => JSON.parse((init as { body: string }).body));

describe("BriefingsScreen", () => {
  it("lists the links by topic, with a no-topic group last", async () => {
    on("/api/briefings", reply(LIST));
    render(<BriefingsScreen />);

    const ai = await screen.findByRole("region", { name: "AI" });
    const none = screen.getByRole("region", { name: "No topic" });

    expect(within(ai).getAllByTestId("briefing").map((li) => li.textContent)).toEqual([
      "Title aexample.com · 2026-11-02 · AI news · 3 briefingsPreviewIngestDismiss",
      "Title bexample.com · 2026-11-02 · 1 briefingPreviewIngestDismiss",
    ]);
    expect(within(none).getAllByTestId("briefing")).toHaveLength(1);
    expect(screen.getByText(`Last pull: ${formatWhen("2026-11-10T12:00:00Z")}`)).toBeInTheDocument();
    expect(usage()).toEqual([{ kind: "screen_view", name: "briefings", detail: null }]);
  });

  it("shows a title with Markdown or HTML as plain text", async () => {
    const title = "**Bold** <img src=x onerror=alert(1)> [link](https://evil.example)";
    on("/api/briefings", reply({ ...LIST, groups: [{ topic_id: null, topic_name: null, links: [row("a", { title })] }] }));
    const { container } = render(<BriefingsScreen />);

    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("strong")?.innerHTML).toBe(
      "**Bold** &lt;img src=x onerror=alert(1)&gt; [link](https://evil.example)",
    );
    expect(screen.queryByRole("link", { name: "link" })).toBeNull();
  });

  it("opens Preview in a new tab without leaking the opener, and logs it", async () => {
    on("/api/briefings", reply(LIST));
    render(<BriefingsScreen />);
    const [preview] = await screen.findAllByRole("link", { name: "Preview" });

    expect(preview).toHaveAttribute("href", "https://example.com/a");
    expect(preview).toHaveAttribute("target", "_blank");
    expect(preview).toHaveAttribute("rel", "noopener noreferrer");
    fireEvent.click(preview);

    expect(usage()).toContainEqual({ kind: "action", name: "briefing_preview", detail: "https://example.com/a" });
  });

  it("dismisses a link: the row goes, the server is told, and it is logged", async () => {
    on("/api/briefings", reply(LIST));
    on("/api/briefings/c/dismiss", reply(null, true, 204));
    render(<BriefingsScreen />);
    const none = await screen.findByRole("region", { name: "No topic" });

    fireEvent.click(within(none).getByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(screen.queryByRole("region", { name: "No topic" })).toBeNull());
    expect(fetchMock).toHaveBeenCalledWith("/api/briefings/c/dismiss", { method: "POST" });
    expect(usage()).toContainEqual({ kind: "action", name: "briefing_dismiss", detail: "https://example.com/c" });
    expect(screen.getAllByTestId("briefing")).toHaveLength(2);
  });

  it("keeps the row and says why when a dismiss fails", async () => {
    on("/api/briefings", reply(LIST));
    on("/api/briefings/c/dismiss", reply({ detail: "No such link." }, false, 404));
    render(<BriefingsScreen />);
    const none = await screen.findByRole("region", { name: "No topic" });

    fireEvent.click(within(none).getByRole("button", { name: "Dismiss" }));

    expect(await within(none).findByRole("alert")).toHaveTextContent("No such link.");
    expect(within(none).getByRole("button", { name: "Dismiss" })).toBeEnabled();
    expect(usage().map((u) => u.name)).toEqual(["briefings"].map(() => "briefings"));
  });

  it("ingests through kai with the group's topic, then the row leaves the list", async () => {
    on("/api/briefings", reply(LIST));
    on(
      "/api/ingests",
      reply(job({ url: "https://example.com/a", title: "Title a", status: "done", message: "Added to the vault: Title a", note_path: "inbox/Title a.md", new_note: true })),
    );
    render(<BriefingsScreen />);
    const ai = await screen.findByRole("region", { name: "AI" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/ai/status"));

    fireEvent.click(within(ai).getAllByRole("button", { name: "Ingest" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Send to kai" }));

    const added = await screen.findByRole("list", { name: "Added to the vault" });
    expect(added).toHaveTextContent("Title a");
    expect(fetchMock).toHaveBeenCalledWith("/api/ingests", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ url: "https://example.com/a", title: "Title a", topic: "ai" }),
    });
    await waitFor(() => expect(screen.getAllByTestId("briefing")).toHaveLength(2));
    expect(within(added).getByRole("link", { name: "Open note" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=My%20Vault&file=inbox%2FTitle%20a",
    );
    expect(screen.queryByText("Title a", { selector: "strong" })).toBeInTheDocument();
    expect(usage()).toContainEqual({ kind: "action", name: "briefing_ingest", detail: "https://example.com/a" });
  });

  it("shows the links added earlier, with their notes, after a refresh", async () => {
    const earlier = [
      { id: "z", title: "Old one", message: "Added to the vault: Old one", note_path: "inbox/Old one.md", finished_at: "2026-11-10T10:00:00Z" },
      { id: "y", title: "No note", message: "Already in the vault", note_path: null, finished_at: null },
    ];
    on("/api/briefings", reply({ ...LIST, added: earlier }));
    render(<BriefingsScreen />);

    const added = await screen.findByRole("list", { name: "Added to the vault" });
    await waitFor(() => expect(within(added).getAllByRole("link", { name: "Open note" })).toHaveLength(1));

    expect(within(added).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Old one Added to the vault: Old one Open note",
      "No note Already in the vault ",
    ]);
    expect(within(added).getByRole("link", { name: "Open note" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=My%20Vault&file=inbox%2FOld%20one",
    );
  });

  it("marks every link processed after a confirm step, and Cancel changes nothing", async () => {
    on("/api/briefings", reply({ ...LIST, pending_checks: 3 }));
    on("/api/briefings/process-all", reply({ count: 7 }));
    render(<BriefingsScreen />);
    await screen.findByRole("region", { name: "AI" });

    fireEvent.click(screen.getByRole("button", { name: "Mark all as processed" }));
    expect(screen.getByText(/Mark all 3 links as processed\?/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Mark all as processed" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/briefings/process-all", { method: "POST" });

    fireEvent.click(screen.getByRole("button", { name: "Mark all as processed" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, mark them" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Marked 7 links as processed.");
    expect(fetchMock).toHaveBeenCalledWith("/api/briefings/process-all", { method: "POST" });
    expect(screen.queryAllByTestId("briefing")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Mark all as processed" })).not.toBeInTheDocument();
    expect(screen.getByText(/Nothing to read/)).toBeInTheDocument();
    expect(usage()).toContainEqual({ kind: "action", name: "briefing_process_all", detail: "7" });
  });

  it("shows the error and keeps the list when marking fails", async () => {
    on("/api/briefings", reply(LIST));
    on("/api/briefings/process-all", reply({ detail: "Disk full." }, false, 500));
    render(<BriefingsScreen />);
    await screen.findByRole("region", { name: "AI" });

    fireEvent.click(screen.getByRole("button", { name: "Mark all as processed" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, mark them" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Disk full.");
    expect(screen.getAllByTestId("briefing")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Yes, mark them" })).toBeEnabled();
  });

  it("has no mark-all button when the list is empty", async () => {
    on("/api/briefings", reply({ groups: [], added: [], pending_checks: 0, last_pull: null }));
    render(<BriefingsScreen />);
    await screen.findByText(/Nothing to read/);

    expect(screen.queryByRole("button", { name: "Mark all as processed" })).not.toBeInTheDocument();
  });

  it("sends no topic for the no-topic group", async () => {
    on("/api/briefings", reply(LIST));
    on("/api/ingests", reply(job({ status: "queued" })));
    render(<BriefingsScreen />);
    const none = await screen.findByRole("region", { name: "No topic" });

    fireEvent.click(within(none).getByRole("button", { name: "Ingest" }));
    fireEvent.click(screen.getByRole("button", { name: "Send to kai" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/ingests", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ url: "https://example.com/c", title: "Title c", topic: null }),
      }),
    );
    expect(screen.getAllByTestId("briefing")).toHaveLength(3);
  });

  it("says what to do when the list is empty", async () => {
    on("/api/briefings", reply({ groups: [], added: [], pending_checks: 0, last_pull: null }));
    render(<BriefingsScreen />);

    expect(await screen.findByText(/Nothing to read\./)).toHaveTextContent(
      "Nothing to read. Run compass briefings pull to fetch your briefings.",
    );
    expect(screen.getByText("Not pulled yet.")).toBeInTheDocument();
  });

  it("says how many new links are still being checked", async () => {
    on("/api/briefings", reply({ ...LIST, pending_checks: 4 }));
    render(<BriefingsScreen />);

    expect(
      await screen.findByText(
        `Last pull: ${formatWhen("2026-11-10T12:00:00Z")} · 4 new links are still being checked and show up after the next pull.`,
      ),
    ).toBeInTheDocument();
  });

  it("shows the error when the list cannot be loaded", async () => {
    on("/api/briefings", reply({ detail: "topics file broken" }, false, 500));
    render(<BriefingsScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("topics file broken");
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("still lists links when the vault name cannot be read", async () => {
    routes["/api/ai/status"] = [reply({ detail: "x" }, false, 500)];
    on("/api/briefings", reply(LIST));
    render(<BriefingsScreen />);

    expect(await screen.findAllByTestId("briefing")).toHaveLength(3);
  });
});

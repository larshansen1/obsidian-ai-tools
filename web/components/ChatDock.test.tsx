import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatDock, ToolCard } from "./ChatDock";

let pathname = "/topics/big";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const STATUS = {
  configured: true,
  model: "m/x",
  vault_name: "vault",
  month_spend_usd: 0.5,
  monthly_limit_usd: 10,
  action_limit_usd: 0.25,
};

function mockApi(history: unknown = null) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.startsWith("/api/ai/status")) return { ok: true, json: async () => STATUS };
    if (url.startsWith("/api/chat/history")) return { ok: true, json: async () => ({ messages: history }) };
    return { ok: true, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  pathname = "/topics/big";
  vi.unstubAllGlobals();
});

describe("ChatDock", () => {
  it("shows the screen and keeps the panel closed until asked", () => {
    mockApi();
    render(
      <ChatDock>
        <p>screen content</p>
      </ChatDock>,
    );

    expect(screen.getByText("screen content")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Chat" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByLabelText("Chat", { selector: "aside", exact: true })).not.toBeVisible();
  });

  it("opens and closes the panel and shows monthly spend", async () => {
    mockApi();
    render(<ChatDock>x</ChatDock>);

    fireEvent.click(screen.getByRole("button", { name: "Chat" }));

    expect(screen.getByRole("button", { name: "Close chat" })).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("m/x · this month $0.50 of $10.00")).toBeInTheDocument();
    expect(screen.getByText("Ask about this topic, for example: what am I missing?")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  });

  it("loads the saved conversation for the topic", async () => {
    const fetchMock = mockApi();
    render(<ChatDock>x</ChatDock>);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/chat/history?topic=big"),
    );
  });

  it("uses the shared conversation on the topic map", async () => {
    pathname = "/";
    const fetchMock = mockApi();
    render(<ChatDock>x</ChatDock>);
    fireEvent.click(screen.getByRole("button", { name: "Chat" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/chat/history"));
    expect(screen.getByText("Ask about your vault.")).toBeVisible();
  });
});

describe("ToolCard", () => {
  it("folds a search into one line with the query and count", () => {
    render(
      <ToolCard
        toolName="search_notes"
        args={{ query: "sleep" }}
        result={[{ path: "a.md", title: "A" }]}
      />,
    );

    expect(screen.getByText('Searched for "sleep": 1 note')).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "A" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=&file=a",
    );
  });

  it("pluralizes and lists tags with counts", () => {
    render(
      <ToolCard
        toolName="topic_tags"
        result={[
          { tag: "adhd", notes: 5 },
          { tag: "sleep", notes: 2 },
        ]}
      />,
    );

    expect(screen.getByText("Looked at 2 tags on the topic")).toBeInTheDocument();
    expect(screen.getByText("adhd (5), sleep (2)")).toBeInTheDocument();
  });

  it("shows a tool error as one line, and a pending call as running", () => {
    const { rerender } = render(<ToolCard toolName="read_note" result={{ error: "No such note: x" }} />);
    expect(screen.getByText("No such note: x")).toBeInTheDocument();

    rerender(<ToolCard toolName="read_note" />);
    expect(screen.getByText("Running read_note…")).toBeInTheDocument();
  });

  it("summarises topic stats", () => {
    render(<ToolCard toolName="topic_stats" result={{ name: "Big", note_count: 1, evergreens: 2 }} />);

    expect(screen.getByText("Big: 1 note, 2 evergreens")).toBeInTheDocument();
  });
});

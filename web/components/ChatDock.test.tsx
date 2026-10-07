import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatDock } from "./ChatDock";

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

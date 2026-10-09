import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IngestButton } from "./IngestButton";
import { NOT_RUNNING_DETAIL, job } from "../lib/ingestFixture";

const fetchMock = vi.fn();
const JSON_HEADERS = { "Content-Type": "application/json" };

function respond(body: unknown, ok = true, status = 200) {
  fetchMock.mockResolvedValueOnce({ ok, status, json: () => Promise.resolve(body) });
}

function renderButton() {
  render(<IngestButton url="https://example.org/p" title="Paper" topic="big" vault="My Vault" pollMs={1000} />);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("IngestButton", () => {
  it("asks first, then sends the source to kai", async () => {
    respond(job({ status: "queued", message: "Sent to kai." }));
    renderButton();

    fireEvent.click(screen.getByRole("button", { name: "Ingest" }));

    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send to kai" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Queued: Sent to kai.");
    expect(fetchMock).toHaveBeenCalledWith("/api/ingests", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ url: "https://example.org/p", title: "Paper", topic: "big" }),
    });
  });

  it("sends nothing when cancelled", () => {
    renderButton();

    fireEvent.click(screen.getByRole("button", { name: "Ingest" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Ingest" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("says when kai serve is not running and stays ready to retry", async () => {
    respond({ detail: NOT_RUNNING_DETAIL }, false, 503);
    renderButton();

    fireEvent.click(screen.getByRole("button", { name: "Ingest" }));
    fireEvent.click(screen.getByRole("button", { name: "Send to kai" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(NOT_RUNNING_DETAIL);
    expect(screen.getByRole("button", { name: "Send to kai" })).toBeEnabled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("polls a queued job until kai is done, then links the note", async () => {
    vi.useFakeTimers();
    respond(job({ status: "queued", message: "Sent to kai." }));
    respond(job({ status: "queued", message: "Sent to kai." }));
    respond(job({ status: "done", message: "Added to the vault: Paper", note_path: "inbox/Paper.md", new_note: true }));
    renderButton();

    fireEvent.click(screen.getByRole("button", { name: "Ingest" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send to kai" }));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Queued: Sent to kai.");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(999);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchMock).toHaveBeenLastCalledWith("/api/ingests/j1", { method: "GET", headers: undefined, body: undefined });
    expect(screen.getByRole("status")).toHaveTextContent("Queued: Sent to kai.");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Done: Added to the vault: Paper");
    expect(screen.getByRole("link", { name: "Open note" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=My%20Vault&file=inbox%2FPaper",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("shows why kai failed and offers to try again", async () => {
    respond(job({ status: "failed", message: "kai could not ingest it: Page not found" }));
    renderButton();

    fireEvent.click(screen.getByRole("button", { name: "Ingest" }));
    fireEvent.click(screen.getByRole("button", { name: "Send to kai" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Failed: kai could not ingest it: Page not found");
    expect(screen.queryByRole("link", { name: "Open note" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(screen.getByRole("button", { name: "Ingest" })).toBeInTheDocument();
  });
});

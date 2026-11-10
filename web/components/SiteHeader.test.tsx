import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SiteHeader } from "./SiteHeader";

let pathname = "/";
const push = vi.fn();
vi.mock("next/navigation", () => ({ usePathname: () => pathname, useRouter: () => ({ push }) }));

const TOPICS = [
  { id: "big", name: "Big", tags: ["x"], note_count: 1234, below_min_notes: false },
  { id: "tiny one", name: "Tiny", tags: ["y"], note_count: 1, below_min_notes: true },
];

const fetchMock = vi.fn();

beforeEach(() => {
  pathname = "/";
  push.mockReset();
  fetchMock.mockReset();
  fetchMock.mockImplementation((url: string) =>
    url === "/api/topics"
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ min_notes: 3, topics: TOPICS }) })
      : Promise.resolve({ ok: true, status: 204 }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

const options = () => screen.getAllByRole("option").map((o) => [o.getAttribute("value"), o.textContent]);

describe("SiteHeader", () => {
  it("links the map and the changes list, and marks the screen that is open", async () => {
    render(<SiteHeader />);
    await screen.findByRole("combobox", { name: "Go to topic" });

    const links = screen.getAllByRole("link");
    expect(links.map((l) => [l.textContent, l.getAttribute("href"), l.getAttribute("aria-current")])).toEqual([
      ["Topic map", "/", "page"],
      ["Recent changes", "/changes", null],
    ]);
  });

  it("lists every topic with its note count, small topics too", async () => {
    render(<SiteHeader />);
    const picker = await screen.findByRole("combobox", { name: "Go to topic" });

    expect(options()).toEqual([
      ["", "Go to topic…"],
      ["big", "Big (1,234 notes)"],
      ["tiny one", "Tiny (1 note)"],
    ]);
    expect(picker).toHaveValue("");
    expect(fetchMock).toHaveBeenCalledWith("/api/topics");
  });

  it("opens the picked topic page and logs it", async () => {
    render(<SiteHeader />);
    const picker = await screen.findByRole("combobox", { name: "Go to topic" });

    fireEvent.change(picker, { target: { value: "tiny one" } });

    expect(push.mock.calls).toEqual([["/topics/tiny%20one?window=30"]]);
    expect(fetchMock).toHaveBeenCalledWith("/api/usage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "action", name: "pick_topic", detail: "tiny one" }),
    });
  });

  it("goes nowhere when the placeholder is picked", async () => {
    pathname = "/topics/big";
    render(<SiteHeader />);
    const picker = await screen.findByRole("combobox", { name: "Go to topic" });

    fireEvent.change(picker, { target: { value: "" } });

    expect(push.mock.calls).toEqual([]);
  });

  it("shows which topic is open, and marks neither link", async () => {
    pathname = "/topics/tiny%20one";
    render(<SiteHeader />);

    expect(await screen.findByRole("combobox", { name: "Go to topic" })).toHaveValue("tiny one");
    expect(screen.getAllByRole("link").map((l) => l.getAttribute("aria-current"))).toEqual([null, null]);
  });

  it("shows the placeholder on a topic page that is not in the list", async () => {
    pathname = "/topics/gone";
    render(<SiteHeader />);

    expect(await screen.findByRole("combobox", { name: "Go to topic" })).toHaveValue("");
  });

  it("keeps the links and leaves the picker out when the topics cannot be loaded", async () => {
    fetchMock.mockImplementation(() => Promise.resolve({ ok: false, status: 500 }));
    render(<SiteHeader />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/topics"));
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["Topic map", "Recent changes"]);
  });
});

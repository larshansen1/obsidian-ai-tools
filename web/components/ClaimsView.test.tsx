import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CLAIMS } from "../lib/claimsFixture";
import { Agreement, ClaimsTable } from "./ClaimsView";

describe("ClaimsTable", () => {
  it("groups claims as supporting or pushing back, each linking to its note", () => {
    render(<ClaimsTable view={CLAIMS} vault="v" />);

    expect(screen.getByText("Do agents need plans?")).toBeInTheDocument();
    const supporting = within(screen.getByRole("list", { name: "Supporting" }));
    const pushing = within(screen.getByRole("list", { name: "Pushing back" }));
    expect(supporting.getByRole("listitem").textContent).toBe("Plans help. A");
    expect(supporting.getByRole("link", { name: "A" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=v&file=notes%2Fa",
    );
    expect(pushing.getByRole("listitem").textContent).toBe("Plans fail. K");
    expect(pushing.getByRole("link", { name: "K" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=v&file=notes%2Fk",
    );
    expect(screen.getByText("1 claims do not bear on this question.")).toBeInTheDocument();
  });

  it("says what each side points to", () => {
    render(<ClaimsTable view={CLAIMS} vault="v" />);

    expect(screen.getByText("Points to yes")).toBeInTheDocument();
    expect(screen.getByText("Points to no")).toBeInTheDocument();
  });

  it("says None for an empty side and hides the unrelated line at zero", () => {
    render(<ClaimsTable view={{ ...CLAIMS, pushing_back: [], unrelated: 0 }} vault="v" />);

    expect(screen.getByText("Pushing back (0)")).toBeInTheDocument();
    expect(screen.getByText("None.")).toBeInTheDocument();
    expect(screen.queryByText(/do not bear/)).toBeNull();
  });

  it("asks for a question before sorting", () => {
    render(<ClaimsTable view={{ ...CLAIMS, question: null }} vault="v" />);

    expect(screen.getByText("Pick a question to sort the 3 claims into two sides.")).toBeInTheDocument();
    expect(screen.queryByTestId("claims-table")).toBeNull();
  });
});

describe("Agreement", () => {
  it("leads with my evergreen, then the AI's line, the counts and the closest notes", () => {
    render(<Agreement view={CLAIMS} vault="v" />);

    const item = within(screen.getByTestId("agreement")).getAllByRole("listitem")[0];
    expect(item.textContent).toBe("Ev Agents need tools.2 claims · 2 notes · 1 not linkedKB");
    const heading = within(item).getByRole("heading", { level: 4 });
    expect(within(heading).getByRole("link", { name: "Ev" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=v&file=notes%2Fevergreen%2Fev",
    );
    expect(within(screen.getByTestId("closest-notes")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "K",
      "B",
    ]);
    // Two notes and nothing to link from here: no "Show all".
    expect(within(item).queryByText(/Show all/)).toBeNull();
  });

  it("uses the shared point as the heading when no evergreen matches", () => {
    const shared = [{ ...CLAIMS.shared[0], evergreen: null, unlinked_notes: [] }];
    render(<Agreement view={{ ...CLAIMS, shared }} vault="v" />);

    expect(screen.getByTestId("agreement").textContent).toBe(
      "Agents need tools. 2 claims · 2 notes · no evergreen matches yetKB",
    );
    expect(screen.getByRole("heading", { level: 4 }).textContent).toBe("Agents need tools. ");
  });

  it("shows the three closest notes one per line and the rest behind Show all", () => {
    const claims = ["a", "b", "c", "d", "e"].map((n, i) => ({ id: `x${i}`, text: n, path: `notes/${n}.md`, title: n.toUpperCase() }));
    const shared = [{ ...CLAIMS.shared[0], claims: [...claims, { ...claims[0], id: "x9" }], unlinked_notes: [] }];
    render(<Agreement view={{ ...CLAIMS, shared }} vault="v" />);

    const closest = screen.getByTestId("closest-notes");
    expect(within(closest).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["A", "B", "C"]);
    expect(within(closest).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "obsidian://open?vault=v&file=notes%2Fa",
      "obsidian://open?vault=v&file=notes%2Fb",
      "obsidian://open?vault=v&file=notes%2Fc",
    ]);
    expect(screen.getByText("Show all 5 notes").closest("details")?.open).toBe(false);
    expect(screen.getByText("6 claims · 5 notes")).toBeInTheDocument();
  });

  it("puts the link action with the full list, which marks the unlinked notes", () => {
    const linkAction = vi.fn(() => <button type="button">Link them</button>);
    render(<Agreement view={CLAIMS} vault="v" linkAction={linkAction} />);

    expect(linkAction).toHaveBeenCalledWith("notes/evergreen/ev.md", ["notes/b.md"]);
    expect(screen.getByRole("button", { name: "Link them" }).closest("details")).not.toBeNull();
    const all = screen.getByRole("list", { name: "Notes behind: Agents need tools." });
    expect(within(all).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["K", "B · not linked"]);
  });

  it("says so when nothing is shared", () => {
    render(<Agreement view={{ ...CLAIMS, shared: [] }} vault="v" />);

    expect(screen.getByText("No claim is shared by two or more notes yet.")).toBeInTheDocument();
  });
});

describe("claim picking", () => {
  it("has no checkboxes without a selection", () => {
    render(<ClaimsTable view={CLAIMS} vault="v" />);

    expect(screen.queryAllByRole("checkbox")).toEqual([]);
  });

  it("ticks single claims and a shared claim's whole group", () => {
    const toggle = vi.fn();
    const selection = { selected: new Set(["c2"]), toggle };
    render(
      <>
        <ClaimsTable view={CLAIMS} vault="v" selection={selection} />
        <Agreement view={CLAIMS} vault="v" selection={selection} />
      </>,
    );

    expect(screen.getByRole("checkbox", { name: "Pick claim: Plans help." })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Pick claim: Plans fail." })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Pick claim: Agents need tools." })).not.toBeChecked();
    expect(within(screen.getByRole("list", { name: "Supporting" })).getByRole("listitem").textContent).toBe(
      " Plans help. A",
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "Pick claim: Agents need tools." }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Pick claim: Plans help." }));

    expect(toggle.mock.calls).toEqual([[["c2", "c3"]], [["c1"]]]);
  });

  it("shows a shared claim as ticked when all its claims are", () => {
    render(<Agreement view={CLAIMS} vault="v" selection={{ selected: new Set(["c2", "c3"]), toggle: vi.fn() }} />);

    expect(screen.getByRole("checkbox", { name: "Pick claim: Agents need tools." })).toBeChecked();
  });
});

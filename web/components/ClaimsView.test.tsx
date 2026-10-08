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
  it("shows the shared claim, its evergreen and the notes that do not link to it", () => {
    render(<Agreement view={CLAIMS} vault="v" />);

    const item = within(screen.getByTestId("agreement")).getByRole("listitem");
    expect(item.textContent).toBe(
      "Agents need tools.In 2 claims from 2 notes · matches evergreen EvSupport it but do not link to it: B",
    );
    expect(within(item).getByRole("link", { name: "Ev" })).toHaveAttribute(
      "href",
      "obsidian://open?vault=v&file=notes%2Fevergreen%2Fev",
    );
  });

  it("says when no evergreen matches and nothing is unlinked", () => {
    const shared = [{ ...CLAIMS.shared[0], evergreen: null, unlinked_notes: [] }];
    render(<Agreement view={{ ...CLAIMS, shared }} vault="v" />);

    expect(screen.getByTestId("agreement").textContent).toBe(
      "Agents need tools.In 2 claims from 2 notes · no evergreen matches yet",
    );
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

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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

import type { ClaimsView } from "./claims";

export const CLAIMS: ClaimsView = {
  topic: "big",
  read_notes: 3,
  pending_notes: 0,
  claim_count: 3,
  questions: ["Do agents need plans?", "Do tools matter?"],
  question: "Do agents need plans?",
  supporting: [{ id: "c1", text: "Plans help.", path: "notes/a.md", title: "A" }],
  pushing_back: [{ id: "c2", text: "Plans fail.", path: "notes/k.md", title: "K" }],
  unrelated: 1,
  shared: [
    {
      text: "Agents need tools.",
      claims: [
        { id: "c2", text: "Plans fail.", path: "notes/k.md", title: "K" },
        { id: "c3", text: "Tools help.", path: "notes/b.md", title: "B" },
      ],
      evergreen: { path: "notes/evergreen/ev.md", title: "Ev", created: null },
      unlinked_notes: [{ path: "notes/b.md", title: "B", created: null }],
    },
  ],
  stale: false,
};

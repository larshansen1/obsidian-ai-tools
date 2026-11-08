import type { ClaimsView, QuestionKind, SuggestedQuestion } from "./claims";

// A suggested question anchored in claims from two notes.
export function suggested(text: string, kind: QuestionKind = "tension"): SuggestedQuestion {
  return {
    text,
    kind,
    why: `Why: ${text}`,
    anchors: [
      { id: "c1", text: "Plans help.", path: "notes/a.md", title: "A" },
      { id: "c2", text: "Plans fail.", path: "notes/k.md", title: "K" },
    ],
  };
}

export const CLAIMS: ClaimsView = {
  topic: "big",
  read_notes: 3,
  pending_notes: 0,
  claim_count: 3,
  questions: [suggested("Do agents need plans?"), suggested("Do tools matter?")],
  question: "Do agents need plans?",
  supporting: [{ id: "c1", text: "Plans help.", path: "notes/a.md", title: "A" }],
  pushing_back: [{ id: "c2", text: "Plans fail.", path: "notes/k.md", title: "K" }],
  unrelated: 1,
  summary: {
    bottom_line: "The notes lean yes.",
    agree: ["Plans matter."],
    split: ["Yes rests on studies; no on experience."],
    missing: ["A direct comparison."],
    read: [{ path: "notes/a.md", title: "A", created: null }],
  },
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
  next_step: "done",
};

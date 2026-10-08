// Claims across sources and where they agree (T3, T4, Q6).

import type { NoteRef } from "./topicPage";

export type ClaimItem = { id: string; text: string; path: string; title: string };

export type SharedClaim = {
  text: string;
  claims: ClaimItem[];
  evergreen: NoteRef | null;
  unlinked_notes: NoteRef[];
};

// What to do next: read notes, suggest questions and find agreement, sort by the question, or nothing.
export type NextStep = "read" | "analyse" | "sort" | "done";

export type ClaimsView = {
  topic: string;
  read_notes: number;
  pending_notes: number;
  claim_count: number;
  questions: string[];
  question: string | null;
  supporting: ClaimItem[];
  pushing_back: ClaimItem[];
  unrelated: number;
  shared: SharedClaim[];
  stale: boolean;
  next_step: NextStep;
};

export type RunStatus = "done" | "needs_approval" | "not_configured" | "failed";

export type ClaimsRun = {
  status: RunStatus;
  message: string | null;
  estimate_usd: number | null;
  view: ClaimsView;
};

async function failure(response: Response): Promise<Error> {
  const body = await response.json().catch(() => ({}));
  return new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
}

function claimsUrl(topic: string, tail = ""): string {
  return `/api/topics/${encodeURIComponent(topic)}/claims${tail}`;
}

export async function fetchClaims(topic: string): Promise<ClaimsView> {
  const response = await fetch(claimsUrl(topic));
  if (!response.ok) throw await failure(response);
  return response.json();
}

export async function runClaims(topic: string, approved: boolean): Promise<ClaimsRun> {
  const response = await fetch(claimsUrl(topic, "/run"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved }),
  });
  if (!response.ok) throw await failure(response);
  return response.json();
}

export async function saveQuestion(topic: string, question: string): Promise<ClaimsView> {
  const response = await fetch(claimsUrl(topic, "/question"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw await failure(response);
  return response.json();
}

/** The button for the step that is next, named after what it does. */
export function runLabel(view: ClaimsView): string | null {
  if (view.next_step === "read") return view.claim_count === 0 ? "Read notes" : "Read more notes";
  if (view.next_step === "analyse") return "Suggest questions and find agreement";
  if (view.next_step === "sort") return "Sort claims by this question";
  return null;
}

export type StepState = "done" | "next" | "waiting";

export type StepStates = { read: StepState; question: StepState; sides: StepState; agree: StepState };

/** Where each of the four steps stands, so the screen can say what is done and what is next. */
export function stepStates(view: ClaimsView): StepStates {
  const reading = view.next_step === "read";
  const analysing = view.next_step === "analyse";
  const hasQuestion = view.question !== null;
  return {
    read: reading ? "next" : "done",
    question: reading ? "waiting" : analysing || !hasQuestion ? "next" : "done",
    sides: reading || analysing || !hasQuestion ? "waiting" : view.next_step === "sort" ? "next" : "done",
    agree: reading || analysing ? "waiting" : "done",
  };
}

export function progressText(view: ClaimsView): string {
  const total = view.read_notes + view.pending_notes;
  const base = `Read ${view.read_notes} of ${total} notes, ${view.claim_count} claims.`;
  return view.pending_notes > 0 && view.read_notes > 0 ? `${base} Run again to read the rest.` : base;
}

// Claims across sources and where they agree (T3, T4, Q6).

import type { NoteRef } from "./topicPage";

export type ClaimItem = { id: string; text: string; path: string; title: string };

export type SharedClaim = {
  text: string;
  claims: ClaimItem[];
  evergreen: NoteRef | null;
  unlinked_notes: NoteRef[];
};

// tension: notes that would answer differently (yes/no, can be sorted into sides). recurring: an
// idea many notes repeat but never examine. carry_over: does a principle hold in another kind of system?
export type QuestionKind = "tension" | "recurring" | "carry_over";

export type SuggestedQuestion = {
  text: string;
  kind: QuestionKind;
  why: string;
  anchors: ClaimItem[];
};

// How the sorted claims split on the chosen question, in plain language (#169).
export type SplitSummary = {
  bottom_line: string;
  agree: string[];
  split: string[];
  missing: string[];
  read: NoteRef[];
};

// What to do next: read notes, suggest questions and find agreement, sort by the question, or nothing.
export type NextStep = "read" | "analyse" | "sort" | "done";

export type ClaimsView = {
  topic: string;
  read_notes: number;
  pending_notes: number;
  claim_count: number;
  questions: SuggestedQuestion[];
  question: string | null;
  supporting: ClaimItem[];
  pushing_back: ClaimItem[];
  unrelated: number;
  summary: SplitSummary | null;
  shared: SharedClaim[];
  stale: boolean;
  next_step: NextStep;
  // The answer shown was made before notes were added or changed; a run updates it.
  answer_outdated: boolean;
  // False when the suggested questions, or "Where they agree", are older than the claims or evergreens.
  questions_current: boolean;
  agreement_current: boolean;
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
  if (view.next_step === "sort") return "Sort and summarise";
  return null;
}

/** The line over an answer made before notes were added or changed; null while it is current. */
export function outdatedText(view: ClaimsView): string | null {
  if (!view.answer_outdated) return null;
  const n = view.pending_notes;
  if (n === 0) return "Your notes changed since this answer.";
  return `${n} new or changed ${n === 1 ? "note" : "notes"} since this answer.`;
}

export type OpenWork = { label: string; why: string };

/**
 * What is still open next to an answer that is up to date (#183): the button's name and one
 * line on why it is there. Null when the answer itself needs work, or nothing is open.
 */
export function openWork(view: ClaimsView): OpenWork | null {
  if (view.summary === null || view.answer_outdated || view.next_step !== "analyse") return null;
  const size = `It goes through ${view.claim_count.toLocaleString("en-US")} ${view.claim_count === 1 ? "claim" : "claims"}.`;
  const lead = "Your answer is up to date.";
  if (view.questions_current) {
    return {
      label: "Find agreement",
      why: `${lead} “Where they agree” has not been checked against your current claims and evergreens. ${size}`,
    };
  }
  if (view.agreement_current) {
    return {
      label: "Suggest questions",
      why: `${lead} The suggested questions are older than your notes. ${size}`,
    };
  }
  return {
    label: "Suggest questions and find agreement",
    why: `${lead} The suggested questions and “Where they agree” are older than your notes. ${size}`,
  };
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

// Claims a draft from the answer starts from: for and against in turn, up to `perSide` each, so
// both sides reach the note. The summary carries the rest of the picture.
export const ANSWER_CLAIMS_PER_SIDE = 15;

export function answerClaimIds(view: ClaimsView, perSide: number = ANSWER_CLAIMS_PER_SIDE): string[] {
  const ids: string[] = [];
  for (let i = 0; i < perSide; i++) {
    if (i < view.supporting.length) ids.push(view.supporting[i].id);
    if (i < view.pushing_back.length) ids.push(view.pushing_back[i].id);
  }
  return ids;
}

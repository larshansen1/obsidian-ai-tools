// Claims across sources and where they agree (T3, T4, Q6).

import type { NoteRef } from "./topicPage";

export type ClaimItem = { id: string; text: string; path: string; title: string };

export type SharedClaim = {
  text: string;
  claims: ClaimItem[];
  evergreen: NoteRef | null;
  unlinked_notes: NoteRef[];
};

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

/** The button label: first read, or catching up with new or changed notes. */
export function runLabel(view: ClaimsView): string {
  return view.claim_count === 0 ? "Find claims" : "Update claims";
}

export function progressText(view: ClaimsView): string {
  const total = view.read_notes + view.pending_notes;
  const base = `Read ${view.read_notes} of ${total} notes, ${view.claim_count} claims.`;
  return view.pending_notes > 0 && view.read_notes > 0 ? `${base} Run again to read the rest.` : base;
}

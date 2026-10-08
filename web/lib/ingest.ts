// Send a source to kai (W3): queued, then done or failed. Drafts of new evergreens (T8).

import type { PendingWrite } from "./writes";

export type IngestStatus = "queued" | "done" | "failed";

export type IngestJob = {
  id: string;
  url: string;
  title: string;
  topic: string | null;
  status: IngestStatus;
  message: string;
  note_path: string | null;
  new_note: boolean;
  requested_at: string;
  finished_at: string | null;
};

export type MonthCount = { month: string; count: number };

export type IngestLog = { jobs: IngestJob[]; per_month: MonthCount[] };

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export function sendToKai(url: string, title: string, topic: string | null): Promise<IngestJob> {
  return send("/api/ingests", "POST", { url, title, topic });
}

export function fetchIngest(id: string): Promise<IngestJob> {
  return send(`/api/ingests/${encodeURIComponent(id)}`, "GET");
}

export function fetchIngests(): Promise<IngestLog> {
  return send("/api/ingests", "GET");
}

export function startDraft(topic: string, claimIds: string[]): Promise<{ body: string }> {
  return send(`/api/topics/${encodeURIComponent(topic)}/evergreen-draft`, "POST", { claim_ids: claimIds });
}

export function planEvergreen(topic: string, title: string, body: string): Promise<PendingWrite> {
  return send("/api/writes/evergreen", "POST", { topic, title, body });
}

// "2026-10" -> the count for that month, 0 when nothing was ingested.
export function monthCount(log: IngestLog, month: string): number {
  return log.per_month.find((m) => m.month === month)?.count ?? 0;
}

// The current month as the API counts it (UTC), e.g. "2026-10".
export function thisMonth(now: Date = new Date()): string {
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

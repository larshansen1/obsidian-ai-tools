import type { IngestJob } from "./ingest";

export const NOT_RUNNING_DETAIL = "kai serve is not running. Start it with `kai serve`, then click Ingest again.";

export function job(fields: Partial<IngestJob>): IngestJob {
  return {
    id: "j1",
    url: "https://example.org/p",
    title: "Paper",
    topic: "big",
    status: "queued",
    message: "Sent to kai.",
    note_path: null,
    new_note: false,
    requested_at: "2026-10-08T12:00:00Z",
    finished_at: null,
    ...fields,
  };
}

// Write-back (W1, W2, W4, W5): every change is previewed, applied on a click, and can be undone.

export const WRITE_TOOLS = ["link_notes", "edit_topic"] as const;

export type DiffLine = { op: "+" | "-" | " " | "@"; text: string };

export type FilePreview = { file: string; lines: DiffLine[] };

export type PendingWrite = { id: string; kind: string; summary: string; files: FilePreview[] };

export type WriteOutcome = {
  id: string;
  status: "written" | "refused" | "undone" | "cancelled";
  message: string;
};

export type LoggedWrite = {
  id: string;
  kind: string;
  summary: string;
  written_at: string;
  files: string[];
  undone: boolean;
};

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
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export function planLinks(evergreen: string, notes: string[]): Promise<PendingWrite> {
  return send("/api/writes/links", "POST", { evergreen, notes });
}

export function planTopicTags(topic: string, add: string[], remove: string[]): Promise<PendingWrite> {
  return send(`/api/topics/${encodeURIComponent(topic)}/tags`, "POST", { add, remove });
}

export function applyWrite(id: string): Promise<WriteOutcome> {
  return send(`/api/writes/${encodeURIComponent(id)}/apply`, "POST");
}

export async function cancelWrite(id: string): Promise<WriteOutcome> {
  await send<void>(`/api/writes/${encodeURIComponent(id)}`, "DELETE");
  return { id, status: "cancelled", message: "Cancelled. Nothing was written." };
}

export function undoWrite(id: string): Promise<WriteOutcome> {
  return send(`/api/writes/${encodeURIComponent(id)}/undo`, "POST");
}

export function fetchWrites(): Promise<LoggedWrite[]> {
  return send("/api/writes", "GET");
}

// "a, b c" -> ["a", "b", "c"]: tags typed into one box.
export function splitTags(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((t) => t.replace(/^#+/, ""))
    .filter((t) => t.length > 0);
}

// "2026-10-08T18:05:00Z" -> "2026-10-08 20:05" in the viewer's own time zone.
export function formatWhen(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

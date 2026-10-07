// Chat helpers: where the user is (C3), citations (C6) and per-topic history (C9).

export type ChatScope = { screen: string; topic: string | null };

export const CONFIRM_TOOL = "confirm_cost";

export function scopeFor(pathname: string): ChatScope {
  const match = /^\/topics\/([^/]+)\/?$/.exec(pathname);
  if (match) return { screen: "topic_page", topic: decodeURIComponent(match[1]) };
  if (pathname === "/") return { screen: "topic_map", topic: null };
  return { screen: "other", topic: null };
}

export type TextPart = { kind: "text"; text: string };
export type NotePart = { kind: "note"; path: string };

const CITATION = /\[\[([^\]\n]+?)\]\]/g;

// The model cites a note as [[vault/path.md]]; split the text around those marks.
export function splitCitations(text: string): (TextPart | NotePart)[] {
  const parts: (TextPart | NotePart)[] = [];
  let last = 0;
  for (const match of text.matchAll(CITATION)) {
    const start = match.index ?? 0;
    if (start > last) parts.push({ kind: "text", text: text.slice(last, start) });
    parts.push({ kind: "note", path: match[1].trim() });
    last = start + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

// Opens the note in Obsidian.
export function noteUrl(vaultName: string, path: string): string {
  const file = path.replace(/\.md$/, "");
  return `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(file)}`;
}

export function noteLabel(path: string): string {
  const name = path.split("/").pop() ?? path;
  return name.replace(/\.md$/, "");
}

export type AiStatus = {
  configured: boolean;
  model: string;
  vault_name: string;
  month_spend_usd: number;
  monthly_limit_usd: number;
  action_limit_usd: number;
};

export async function fetchAiStatus(): Promise<AiStatus> {
  const response = await fetch("/api/ai/status");
  if (!response.ok) throw new Error(`Status ${response.status}`);
  return (await response.json()) as AiStatus;
}

export function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function historyUrl(topic: string | null): string {
  return topic === null ? "/api/chat/history" : `/api/chat/history?topic=${encodeURIComponent(topic)}`;
}

// The thread export is opaque to the server; it only stores and returns it.
export async function loadHistory(topic: string | null): Promise<unknown | null> {
  const response = await fetch(historyUrl(topic));
  if (!response.ok) return null;
  return ((await response.json()) as { messages: unknown | null }).messages;
}

export async function saveHistory(topic: string | null, messages: unknown): Promise<void> {
  await fetch(historyUrl(topic), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
}

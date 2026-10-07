import type { TopicStats, Window } from "./topicMap";

export type SignalKind = "new_and_growing" | "writing_more_than_reading" | "source_concentration";

export type Signal = {
  topic_id: string;
  topic_name: string;
  kind: SignalKind;
  message: string;
  note_count: number;
};

export type NoteRef = { path: string; title: string; created: string | null };

export type TopicPage = {
  window: Window;
  min_notes: number;
  stats: TopicStats;
  tags: string[];
  top_sources: { name: string; notes: number }[];
  unknown_source_notes: number;
  source_types: { source_type: string; notes: number; share: number }[];
  evergreens: (NoteRef & { bridges: { id: string; name: string }[] })[];
  recent_notes: (NoteRef & { duplicates: { path: string; reason: string }[] })[];
  signal: { kind: SignalKind; rules: string; notes: NoteRef[] } | null;
};

export const SIGNAL_KINDS: SignalKind[] = ["new_and_growing", "writing_more_than_reading", "source_concentration"];

export const DUPLICATE_REASONS: Record<string, string> = {
  source_url: "same source link",
  author_title: "same author, similar title",
};

export function isSignalKind(value: string | undefined): value is SignalKind {
  return SIGNAL_KINDS.some((k) => k === value);
}

export function isWindow(value: string | undefined): value is Window {
  return value === "30" || value === "90" || value === "all";
}

/** Where a topic-map signal leads: the topic page, filtered to the notes behind it. */
export function signalHref(signal: Signal, window: Window): string {
  return `/topics/${encodeURIComponent(signal.topic_id)}?signal=${signal.kind}&window=${window}`;
}

export function topicHref(id: string, window: Window): string {
  return `/topics/${encodeURIComponent(id)}?window=${window}`;
}

export async function fetchTopicPage(id: string, window: Window, signal: SignalKind | null): Promise<TopicPage> {
  const query = `window=${window}${signal ? `&signal=${signal}` : ""}`;
  const response = await fetch(`/api/topics/${encodeURIComponent(id)}?${query}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json();
}

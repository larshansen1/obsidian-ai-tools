import type { Signal } from "./topicPage";

export type Window = "30" | "90" | "all";

export type Tiles = {
  total_notes: number;
  notes_last_30: number;
  notes_prior_30: number;
  link_share: number;
  evergreen_count: number;
  inbox_count: number;
};

export type TopicStats = {
  id: string;
  name: string;
  note_count: number;
  window_notes: number;
  momentum: number | null;
  evergreens: number;
  linked_notes: number;
  notes_per_month: { month: string; notes: number }[];
  top_source: { name: string; notes: number } | null;
  below_min_notes: boolean;
  next_step: string;
};

export type TopicMap = {
  window: Window;
  min_notes: number;
  momentum_formula: string;
  tiles: Tiles;
  topics: TopicStats[];
  signals: Signal[];
};

export const WINDOWS: { value: Window; label: string }[] = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "all", label: "All time" },
];

export const NOT_SCANNED_MESSAGE = "No topic data yet. Run `compass scan` first.";

export async function fetchTopicMap(window: Window): Promise<TopicMap> {
  const response = await fetch(`/api/topic-map?window=${window}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json();
}

// Fire and forget: a failed usage log must never break the screen.
export function logUsage(kind: "screen_view" | "action", name: string, detail?: string): void {
  fetch("/api/usage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, name, detail: detail ?? null }),
  }).catch(() => undefined);
}

export function formatMomentum(momentum: number | null): string {
  if (momentum === null) return "n/a";
  const sign = momentum > 0 ? "+" : "";
  return `${sign}${momentum}%`;
}

export function formatShare(share: number): string {
  return `${Math.round(share * 100)}%`;
}

/** Percent change between the last 30 days and the 30 days before; null when there is no base. */
export function tileTrend(last: number, prior: number): number | null {
  if (prior === 0) return null;
  return Math.round(((last - prior) / prior) * 1000) / 10;
}

export type Bubble = {
  id: string;
  x: number;
  y: number;
  r: number;
  muted: boolean;
  hasMomentum: boolean;
};

// Padding leaves room for the biggest bubble and its label beyond the plot edges.
export const CHART = { width: 720, height: 480, padLeft: 72, padRight: 56, padTop: 56, padBottom: 88 };
export const MIN_RADIUS = 6;
export const MAX_RADIUS = 22;
export const MIN_X_RANGE = 100;

function scale(value: number, domainMin: number, domainMax: number, rangeMin: number, rangeMax: number): number {
  if (domainMax === domainMin) return (rangeMin + rangeMax) / 2;
  return rangeMin + ((value - domainMin) / (domainMax - domainMin)) * (rangeMax - rangeMin);
}

/** x domain is symmetric around 0 and at least ±100%, so a small topic cannot stretch the chart. */
export function xDomain(topics: TopicStats[]): number {
  const visible = topics.filter((t) => !t.below_min_notes && t.momentum !== null);
  return Math.max(MIN_X_RANGE, ...visible.map((t) => Math.abs(t.momentum as number)));
}

export function layoutBubbles(topics: TopicStats[]): Bubble[] {
  const limit = xDomain(topics);
  const maxEvergreens = Math.max(1, ...topics.map((t) => t.evergreens));
  const maxCount = Math.max(1, ...topics.map((t) => t.note_count));
  const left = CHART.padLeft;
  const right = CHART.width - CHART.padRight;
  const top = CHART.padTop;
  const bottom = CHART.height - CHART.padBottom;
  return topics.map((t) => {
    const momentum = t.momentum ?? 0;
    const clamped = Math.max(-limit, Math.min(limit, momentum));
    return {
      id: t.id,
      x: scale(clamped, -limit, limit, left, right),
      y: scale(t.evergreens, 0, maxEvergreens, bottom, top),
      r: MIN_RADIUS + (MAX_RADIUS - MIN_RADIUS) * Math.sqrt(t.note_count / maxCount),
      muted: t.below_min_notes,
      hasMomentum: t.momentum !== null,
    };
  });
}

export const LABEL_FONT = 12;
const LABEL_CHAR_WIDTH = 6.4;
const LABEL_GAP = 4;

export type Label = { id: string; x: number; y: number; anchor: "start" | "middle" | "end" };

type Box = { left: number; right: number; top: number; bottom: number };

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/**
 * Puts each name under its bubble. When that spot is taken by an earlier label it tries
 * above, then right, then left, and falls back to below. Bigger bubbles are placed first,
 * so the labels people care about most keep the best spot.
 */
export function placeLabels(bubbles: Bubble[], names: Map<string, string>): Label[] {
  const placed: Box[] = [];
  const result = new Map<string, Label>();
  const order = [...bubbles].sort((a, b) => b.r - a.r || a.id.localeCompare(b.id));
  for (const b of order) {
    const width = (names.get(b.id) ?? b.id).length * LABEL_CHAR_WIDTH;
    const at = (x: number, baseline: number, anchor: Label["anchor"]) => {
      const left = anchor === "middle" ? x - width / 2 : anchor === "start" ? x : x - width;
      const box: Box = { left, right: left + width, top: baseline - LABEL_FONT, bottom: baseline + 2 };
      return { label: { id: b.id, x, y: baseline, anchor }, box };
    };
    const candidates = [
      at(b.x, b.y + b.r + LABEL_FONT + LABEL_GAP - 2, "middle"),
      at(b.x, b.y - b.r - LABEL_GAP, "middle"),
      at(b.x + b.r + LABEL_GAP, b.y + LABEL_FONT / 2 - 2, "start"),
      at(b.x - b.r - LABEL_GAP, b.y + LABEL_FONT / 2 - 2, "end"),
    ];
    const chosen = candidates.find((c) => !placed.some((p) => overlaps(p, c.box))) ?? candidates[0];
    placed.push(chosen.box);
    result.set(b.id, chosen.label);
  }
  return bubbles.map((b) => result.get(b.id) as Label);
}

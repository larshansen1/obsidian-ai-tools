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

// Padding leaves room for the biggest bubble and its label beyond the plot edges. The bottom
// also holds the momentum numbers, the axis names and the size legend (#184).
export const CHART = { width: 720, height: 552, padLeft: 72, padRight: 56, padTop: 56, padBottom: 160 };
// Area follows the note count, so a topic with a quarter of the notes has half the radius.
export const MIN_RADIUS = 5;
export const MAX_RADIUS = 30;
export const MIN_X_RANGE = 100;
// The momentum axis ends on a multiple of this, so its numbers are round.
export const X_STEP = 50;
export const MAX_Y_TICKS = 4;

function scale(value: number, domainMin: number, domainMax: number, rangeMin: number, rangeMax: number): number {
  if (domainMax === domainMin) return (rangeMin + rangeMax) / 2;
  return rangeMin + ((value - domainMin) / (domainMax - domainMin)) * (rangeMax - rangeMin);
}

/**
 * x domain is symmetric around 0 and at least ±100%, so a small topic cannot stretch the chart.
 * It is rounded up to a multiple of X_STEP, so the axis numbers are round.
 */
export function xDomain(topics: TopicStats[]): number {
  const visible = topics.filter((t) => !t.below_min_notes && t.momentum !== null);
  const widest = Math.max(MIN_X_RANGE, ...visible.map((t) => Math.abs(t.momentum as number)));
  return Math.ceil(widest / X_STEP) * X_STEP;
}

const maxEvergreensOf = (topics: TopicStats[]) => Math.max(1, ...topics.map((t) => t.evergreens));
const maxCountOf = (topics: TopicStats[]) => Math.max(1, ...topics.map((t) => t.note_count));

function radius(count: number, maxCount: number): number {
  return Math.max(MIN_RADIUS, MAX_RADIUS * Math.sqrt(count / maxCount));
}

export type Tick = { value: number; pos: number; label: string };

/** Five momentum marks: both ends, halfway to each, and zero in the middle. */
export function xTicks(topics: TopicStats[]): Tick[] {
  const limit = xDomain(topics);
  const left = CHART.padLeft;
  const right = CHART.width - CHART.padRight;
  return [-limit, -limit / 2, 0, limit / 2, limit].map((value) => ({
    value,
    pos: scale(value, -limit, limit, left, right),
    label: formatMomentum(value),
  }));
}

/** Whole evergreen counts from 0 up, at most MAX_Y_TICKS steps, so no mark falls between notes. */
export function yTicks(topics: TopicStats[]): Tick[] {
  const max = maxEvergreensOf(topics);
  const step = Math.ceil(max / MAX_Y_TICKS);
  const top = CHART.padTop;
  const bottom = CHART.height - CHART.padBottom;
  const ticks: Tick[] = [];
  for (let value = 0; value <= max; value += step) {
    ticks.push({ value, pos: scale(value, 0, max, bottom, top), label: String(value) });
  }
  return ticks;
}

const NICE_COUNTS = [1000, 500, 250, 100, 50, 25, 10];

export type SizeMark = { count: number; r: number };

/**
 * Two bubbles for the size legend: a round note count at most half the biggest topic, and the
 * biggest topic itself. Only the biggest when no round count fits under it.
 */
export function sizeLegend(topics: TopicStats[]): SizeMark[] {
  const maxCount = maxCountOf(topics);
  const nice = NICE_COUNTS.find((n) => n * 2 <= maxCount);
  const counts = nice === undefined ? [maxCount] : [nice, maxCount];
  return counts.map((count) => ({ count, r: radius(count, maxCount) }));
}

export function layoutBubbles(topics: TopicStats[]): Bubble[] {
  const limit = xDomain(topics);
  const maxEvergreens = maxEvergreensOf(topics);
  const maxCount = maxCountOf(topics);
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
      r: radius(t.note_count, maxCount),
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

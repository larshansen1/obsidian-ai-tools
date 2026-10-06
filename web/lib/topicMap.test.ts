import { describe, expect, it } from "vitest";
import {
  CHART,
  MAX_RADIUS,
  MIN_RADIUS,
  formatMomentum,
  formatShare,
  layoutBubbles,
  tileTrend,
  xDomain,
  type TopicStats,
} from "./topicMap";

function topic(over: Partial<TopicStats>): TopicStats {
  return {
    id: "t",
    name: "T",
    note_count: 10,
    window_notes: 0,
    momentum: 0,
    evergreens: 0,
    linked_notes: 0,
    notes_per_month: [],
    top_source: null,
    below_min_notes: false,
    next_step: "",
    ...over,
  };
}

describe("formatting", () => {
  it("formats momentum with a sign, and n/a when missing", () => {
    expect(formatMomentum(200)).toBe("+200%");
    expect(formatMomentum(-12.5)).toBe("-12.5%");
    expect(formatMomentum(0)).toBe("0%");
    expect(formatMomentum(null)).toBe("n/a");
  });

  it("rounds a share to whole percent", () => {
    expect(formatShare(0.0649)).toBe("6%");
    expect(formatShare(0.065)).toBe("7%");
  });

  it("computes the tile trend to one decimal, null without a base", () => {
    expect(tileTrend(4, 1)).toBe(300);
    expect(tileTrend(1, 3)).toBe(-66.7);
    expect(tileTrend(5, 0)).toBeNull();
  });
});

describe("xDomain", () => {
  it("is at least 100", () => {
    expect(xDomain([topic({ momentum: 40 })])).toBe(100);
  });

  it("grows to the largest visible momentum, ignoring small topics", () => {
    const topics = [
      topic({ id: "a", momentum: -250 }),
      topic({ id: "b", momentum: 900, below_min_notes: true }),
      topic({ id: "c", momentum: null }),
    ];
    expect(xDomain(topics)).toBe(250);
  });
});

describe("layoutBubbles", () => {
  const left = CHART.padLeft;
  const right = CHART.width - CHART.padRight;
  const top = CHART.padTop;
  const bottom = CHART.height - CHART.padBottom;

  it("puts zero momentum in the middle and the extremes on the edges", () => {
    const [low, mid, high] = layoutBubbles([
      topic({ id: "low", momentum: -100 }),
      topic({ id: "mid", momentum: 0 }),
      topic({ id: "high", momentum: 100 }),
    ]);
    expect(low.x).toBe(left);
    expect(mid.x).toBe((left + right) / 2);
    expect(high.x).toBe(right);
  });

  it("puts no evergreens on the bottom and the most on the top", () => {
    const [none, most] = layoutBubbles([
      topic({ id: "none", evergreens: 0 }),
      topic({ id: "most", evergreens: 4 }),
    ]);
    expect(none.y).toBe(bottom);
    expect(most.y).toBe(top);
  });

  it("sizes the biggest topic at the maximum radius and keeps a minimum", () => {
    const [big, empty] = layoutBubbles([
      topic({ id: "big", note_count: 50 }),
      topic({ id: "empty", note_count: 0 }),
    ]);
    expect(big.r).toBe(MAX_RADIUS);
    expect(empty.r).toBe(MIN_RADIUS);
  });

  it("marks small topics as muted and missing momentum as plotted at the centre", () => {
    const [small, unknown] = layoutBubbles([
      topic({ id: "small", below_min_notes: true, momentum: 80 }),
      topic({ id: "unknown", momentum: null }),
    ]);
    expect(small.muted).toBe(true);
    expect(unknown).toMatchObject({ x: (left + right) / 2, hasMomentum: false, muted: false });
  });

  it("clamps a small topic's extreme momentum to the chart edge", () => {
    const [, wild] = layoutBubbles([
      topic({ id: "ok", momentum: 50 }),
      topic({ id: "wild", momentum: 900, below_min_notes: true }),
    ]);
    expect(wild.x).toBe(right);
  });
});

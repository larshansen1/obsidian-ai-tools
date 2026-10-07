import { describe, expect, it } from "vitest";
import {
  CHART,
  MAX_RADIUS,
  MIN_RADIUS,
  formatMomentum,
  formatShare,
  layoutBubbles,
  placeLabels,
  LABEL_FONT,
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

describe("placeLabels", () => {
  const bubble = (id: string, x: number, y: number, r: number) => ({ id, x, y, r, muted: false, hasMomentum: true });
  const names = new Map([
    ["a", "Alpha"],
    ["b", "Beta"],
    ["c", "Gamma"],
    ["d", "Delta"],
  ]);
  it("puts a lone label under its bubble", () => {
    const [label] = placeLabels([bubble("a", 100, 100, 10)], names);

    expect(label).toEqual({ id: "a", x: 100, y: 100 + 10 + LABEL_FONT + 4 - 2, anchor: "middle" });
  });

  it("moves a label above when the spot below is taken by a bigger bubble's label", () => {
    const labels = placeLabels([bubble("a", 100, 100, 20), bubble("b", 100, 100, 10)], names);

    expect(labels[0]).toMatchObject({ id: "a", anchor: "middle", y: 100 + 20 + LABEL_FONT + 2 });
    expect(labels[1]).toEqual({ id: "b", x: 100, y: 100 - 10 - 4, anchor: "middle" });
  });

  it("tries the right side, then the left side, when below and above are taken", () => {
    const labels = placeLabels(
      ["a", "b", "c", "d"].map((id) => bubble(id, 100, 100, 10)),
      names,
    );

    // Equal sizes are placed in id order: a below, b above, c right, d left.
    expect(labels.map((l) => l.anchor)).toEqual(["middle", "middle", "start", "end"]);
    expect(labels[2]).toEqual({ id: "c", x: 100 + 10 + 4, y: 100 + LABEL_FONT / 2 - 2, anchor: "start" });
    expect(labels[3]).toEqual({ id: "d", x: 100 - 10 - 4, y: 100 + LABEL_FONT / 2 - 2, anchor: "end" });
  });

  it("keeps the first spot when nothing is taken and returns labels in input order", () => {
    const labels = placeLabels([bubble("b", 300, 100, 10), bubble("a", 100, 100, 20)], names);

    expect(labels.map((l) => [l.id, l.anchor, l.y > 100])).toEqual([
      ["b", "middle", true],
      ["a", "middle", true],
    ]);
  });

  it("falls back to below when every spot is taken", () => {
    const crowd = ["a", "b", "c", "d", "e"].map((id) => bubble(id, 100, 100, 10));
    const all = new Map([...names, ["e", "Epsilon"]]);

    const labels = placeLabels(crowd, all);

    expect(labels[4]).toMatchObject({ id: "e", anchor: "middle", y: 100 + 10 + LABEL_FONT + 2 });
  });
});

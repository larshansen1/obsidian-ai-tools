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
  sizeLegend,
  tileTrend,
  xDomain,
  xTicks,
  yTicks,
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

describe("axis numbers", () => {
  const left = CHART.padLeft;
  const right = CHART.width - CHART.padRight;
  const top = CHART.padTop;
  const bottom = CHART.height - CHART.padBottom;

  it("rounds the momentum axis up to the next 50, and leaves an exact 50 alone", () => {
    expect(xDomain([topic({ momentum: 150 })])).toBe(150);
    expect(xDomain([topic({ momentum: -150.1 })])).toBe(200);
    expect(xDomain([topic({ momentum: 100.1 })])).toBe(150);
  });

  it("marks both ends, the halves and zero on the momentum axis", () => {
    expect(xTicks([topic({ momentum: 40 })])).toEqual([
      { value: -100, pos: left, label: "-100%" },
      { value: -50, pos: left + (right - left) / 4, label: "-50%" },
      { value: 0, pos: (left + right) / 2, label: "0%" },
      { value: 50, pos: left + ((right - left) * 3) / 4, label: "+50%" },
      { value: 100, pos: right, label: "+100%" },
    ]);
  });

  it("puts the momentum marks where the bubbles with those values sit", () => {
    const topics = [topic({ id: "a", momentum: -75 }), topic({ id: "b", momentum: 150 })];
    const ticks = xTicks(topics);
    const [a, b] = layoutBubbles(topics);
    expect(ticks.map((t) => t.value)).toEqual([-150, -75, 0, 75, 150]);
    expect(ticks[1].pos).toBe(a.x);
    expect(ticks[4].pos).toBe(b.x);
  });

  it("counts evergreens in whole steps, at most four above zero", () => {
    const values = (max: number) => yTicks([topic({ evergreens: max })]).map((t) => t.value);
    expect(values(0)).toEqual([0, 1]);
    expect(values(1)).toEqual([0, 1]);
    expect(values(4)).toEqual([0, 1, 2, 3, 4]);
    expect(values(5)).toEqual([0, 2, 4]);
    expect(values(8)).toEqual([0, 2, 4, 6, 8]);
    expect(values(9)).toEqual([0, 3, 6, 9]);
  });

  it("puts the evergreen marks where the bubbles with those counts sit", () => {
    expect(yTicks([topic({ evergreens: 2 })])).toEqual([
      { value: 0, pos: bottom, label: "0" },
      { value: 1, pos: (top + bottom) / 2, label: "1" },
      { value: 2, pos: top, label: "2" },
    ]);
  });
});

describe("sizeLegend", () => {
  it("shows a round count and the biggest topic, at the radius a bubble of that size gets", () => {
    const topics = [topic({ id: "a", note_count: 400 }), topic({ id: "b", note_count: 100 })];
    const [, small] = layoutBubbles(topics);
    expect(sizeLegend(topics)).toEqual([
      { count: 100, r: small.r },
      { count: 400, r: MAX_RADIUS },
    ]);
    expect(small.r).toBe(MAX_RADIUS / 2);
  });

  it("picks the largest round count that is at most half the biggest topic", () => {
    const counts = (max: number) => sizeLegend([topic({ note_count: max })]).map((m) => m.count);
    expect(counts(19)).toEqual([19]);
    expect(counts(20)).toEqual([10, 20]);
    expect(counts(499)).toEqual([100, 499]);
    expect(counts(500)).toEqual([250, 500]);
    expect(counts(2000)).toEqual([1000, 2000]);
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

  it("gives a topic with a quarter of the notes half the radius, down to the minimum", () => {
    const [, quarter, floor, under] = layoutBubbles([
      topic({ id: "big", note_count: 3600 }),
      topic({ id: "quarter", note_count: 900 }),
      topic({ id: "floor", note_count: 100 }),
      topic({ id: "under", note_count: 99 }),
    ]);
    expect(quarter.r).toBe(15);
    // 30 * sqrt(100 / 3600) is exactly the minimum radius of 5.
    expect(floor.r).toBe(5);
    expect(under.r).toBe(MIN_RADIUS);
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

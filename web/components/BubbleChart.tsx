import {
  CHART,
  LABEL_FONT,
  layoutBubbles,
  placeLabels,
  sizeLegend,
  xTicks,
  yTicks,
  type TopicStats,
} from "../lib/topicMap";

// Offsets below the bottom of the plot. The numbers sit under the lowest bubble and its name.
const GRID_DROP = 46;
const TICK_ROW = 62;
const NAME_ROW = 82;
const LEGEND_BASE = 150;

type Props = {
  topics: TopicStats[];
  selectedId: string | null;
  onSelect: (id: string) => void;
};

export function BubbleChart({ topics, selectedId, onSelect }: Props) {
  const bubbles = layoutBubbles(topics);
  const names = new Map(topics.map((t) => [t.id, t.name]));
  const labels = new Map(placeLabels(bubbles, names).map((l) => [l.id, l]));
  const plotBottom = CHART.height - CHART.padBottom;
  const midX = (CHART.padLeft + CHART.width - CHART.padRight) / 2;
  const plotLeft = CHART.padLeft - 30;
  const plotRight = CHART.width - CHART.padRight + 24;
  const across = xTicks(topics);
  const up = yTicks(topics);
  const sizes = sizeLegend(topics);
  const legendLeft = CHART.padLeft - 24;
  // Each legend bubble sits on one base line, its count to the right of it.
  const marks = sizes.map((mark, i) => {
    const before = sizes.slice(0, i).reduce((width, m) => width + 2 * m.r + 64, 0);
    return { ...mark, cx: legendLeft + 96 + before + mark.r };
  });
  return (
    <svg
      className="chart"
      viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      role="group"
      aria-label="Topic map: momentum across, evergreens up, bubble size is note count"
    >
      <g data-testid="y-axis" style={{ fill: "var(--muted)" }}>
        {up.map((tick) => (
          <g key={tick.value}>
            {tick.value === 0 ? null : (
              <line x1={plotLeft} x2={plotRight} y1={tick.pos} y2={tick.pos} style={{ stroke: "var(--border)" }} />
            )}
            <text x={plotLeft - 8} y={tick.pos + 4} fontSize={12} textAnchor="end" data-testid="y-tick">
              {tick.label}
            </text>
          </g>
        ))}
      </g>
      <g data-testid="x-axis" style={{ fill: "var(--muted)" }}>
        {across.map((tick) => (
          <g key={tick.value}>
            <line
              x1={tick.pos}
              x2={tick.pos}
              y1={CHART.padTop - 24}
              y2={plotBottom + GRID_DROP}
              style={{ stroke: tick.value === 0 ? "var(--muted)" : "var(--border)" }}
              strokeDasharray={tick.value === 0 ? undefined : "4 4"}
            />
            <text x={tick.pos} y={plotBottom + TICK_ROW} fontSize={12} textAnchor="middle" data-testid="x-tick">
              {tick.label}
            </text>
          </g>
        ))}
      </g>
      <line x1={plotLeft} x2={plotRight} y1={plotBottom} y2={plotBottom} style={{ stroke: "var(--muted)" }} />
      <text x={legendLeft} y={plotBottom + NAME_ROW} fontSize={12} style={{ fill: "var(--muted)" }}>
        ← Slowing
      </text>
      <text x={plotRight} y={plotBottom + NAME_ROW} fontSize={12} textAnchor="end" style={{ fill: "var(--muted)" }}>
        Growing →
      </text>
      <text x={midX} y={plotBottom + NAME_ROW} textAnchor="middle" fontSize={12} style={{ fill: "var(--muted)" }}>
        Momentum
      </text>
      <text x={16} y={16} fontSize={12} style={{ fill: "var(--muted)" }}>
        ↑ Evergreens (worked through)
      </text>
      <g data-testid="size-legend" style={{ fill: "var(--muted)" }}>
        <text x={legendLeft} y={plotBottom + LEGEND_BASE - 4} fontSize={12}>
          Bubble size
        </text>
        {marks.map((mark) => (
          <g key={mark.count} data-testid="size-mark">
            <circle
              cx={mark.cx}
              cy={plotBottom + LEGEND_BASE - mark.r}
              r={mark.r}
              fillOpacity={0.2}
              strokeWidth={1.5}
              style={{ fill: "var(--accent)", stroke: "var(--accent)" }}
            />
            <text x={mark.cx + mark.r + 6} y={plotBottom + LEGEND_BASE - 4} fontSize={12}>
              {mark.count.toLocaleString("en-US")} notes
            </text>
          </g>
        ))}
      </g>
      {[...bubbles].sort((a, b) => b.r - a.r).map((b) => {
        const name = names.get(b.id) ?? b.id;
        const selected = b.id === selectedId;
        return (
          <g
            key={b.id}
            role="button"
            tabIndex={0}
            aria-label={name}
            aria-pressed={selected}
            data-testid={`bubble-${b.id}`}
            style={{ cursor: "pointer" }}
            onClick={() => onSelect(b.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(b.id);
              }
            }}
          >
            <circle
              cx={b.x}
              cy={b.y}
              r={b.r}
              fillOpacity={b.hasMomentum ? 0.45 : 0.2}
              strokeWidth={selected ? 3 : 1.5}
              style={{
                fill: b.muted ? "var(--muted)" : "var(--accent)",
                stroke: selected ? "var(--text)" : b.muted ? "var(--muted)" : "var(--accent)",
              }}
            />
            <text
              x={labels.get(b.id)?.x}
              y={labels.get(b.id)?.y}
              textAnchor={labels.get(b.id)?.anchor}
              fontSize={LABEL_FONT}
              fontWeight={selected ? 700 : 500}
              style={{ fill: "var(--text)", stroke: "var(--card)", strokeWidth: 4, paintOrder: "stroke" }}
            >
              {name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

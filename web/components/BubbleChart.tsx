import { CHART, LABEL_FONT, layoutBubbles, placeLabels, type TopicStats } from "../lib/topicMap";

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
  return (
    <svg
      className="chart"
      viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      role="group"
      aria-label="Topic map: momentum across, evergreens up, bubble size is note count"
    >
      <line x1={midX} x2={midX} y1={CHART.padTop - 24} y2={plotBottom} style={{ stroke: "var(--border)" }} strokeDasharray="4 4" />
      <line x1={CHART.padLeft - 24} x2={CHART.width - CHART.padRight + 24} y1={plotBottom} y2={plotBottom} style={{ stroke: "var(--muted)" }} />
      <text x={CHART.padLeft - 24} y={plotBottom + 56} fontSize={12} style={{ fill: "var(--muted)" }}>
        ← Slowing
      </text>
      <text x={CHART.width - CHART.padRight + 24} y={plotBottom + 56} fontSize={12} textAnchor="end" style={{ fill: "var(--muted)" }}>
        Growing →
      </text>
      <text x={midX} y={plotBottom + 56} textAnchor="middle" fontSize={12} style={{ fill: "var(--muted)" }}>
        Momentum
      </text>
      <text x={16} y={CHART.padTop - 8} fontSize={12} style={{ fill: "var(--muted)" }}>
        ↑ Evergreens
      </text>
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

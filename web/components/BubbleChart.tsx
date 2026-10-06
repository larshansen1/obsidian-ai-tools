import { CHART, layoutBubbles, type TopicStats } from "../lib/topicMap";

type Props = {
  topics: TopicStats[];
  selectedId: string | null;
  onSelect: (id: string) => void;
};

export function BubbleChart({ topics, selectedId, onSelect }: Props) {
  const bubbles = layoutBubbles(topics);
  const names = new Map(topics.map((t) => [t.id, t.name]));
  const midX = (CHART.padLeft + CHART.width - CHART.padRight) / 2;
  return (
    <svg
      viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      role="group"
      aria-label="Topic map: momentum across, evergreens up, bubble size is note count"
      style={{ width: "100%", maxWidth: 720, border: "1px solid #d0d7de", borderRadius: 8, background: "#fff" }}
    >
      <line x1={midX} x2={midX} y1={CHART.padTop} y2={CHART.height - CHART.padBottom} stroke="#d0d7de" />
      <line
        x1={CHART.padLeft}
        x2={CHART.width - CHART.padRight}
        y1={CHART.height - CHART.padBottom}
        y2={CHART.height - CHART.padBottom}
        stroke="#8c959f"
      />
      <text x={CHART.width / 2} y={CHART.height - 8} textAnchor="middle" fontSize={12} fill="#57606a">
        Momentum
      </text>
      <text x={14} y={CHART.height / 2} fontSize={12} fill="#57606a" transform={`rotate(-90 14 ${CHART.height / 2})`} textAnchor="middle">
        Evergreens
      </text>
      {bubbles.map((b) => {
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
              fill={b.muted ? "#d0d7de" : "#0969da"}
              fillOpacity={b.hasMomentum ? 0.55 : 0.25}
              stroke={selected ? "#1f2328" : b.muted ? "#8c959f" : "#0969da"}
              strokeWidth={selected ? 3 : 1}
            />
            <text x={b.x} y={b.y + b.r + 12} textAnchor="middle" fontSize={11} fill="#1f2328">
              {name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

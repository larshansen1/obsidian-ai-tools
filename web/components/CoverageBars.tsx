import type { TypeCount } from "../lib/coverage";

// One bar per source type, scaled to the largest. Shared by the topic page and the chat card.
export function CoverageBars({ byType }: { byType: TypeCount[] }) {
  const most = Math.max(1, ...byType.map((t) => t.count));
  return (
    <ul className="coverage-bars">
      {byType.map((t) => (
        <li key={t.type}>
          <span className="coverage-type">{t.type}</span>
          <span
            className="coverage-bar"
            style={{ width: `${(t.count / most) * 100}%` }}
          />
          <span className="coverage-count">{t.count}</span>
        </li>
      ))}
    </ul>
  );
}

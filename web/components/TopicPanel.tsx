import Link from "next/link";
import { formatMomentum, type TopicStats } from "../lib/topicMap";
import { logUsage } from "../lib/topicMap";

export function TopicPanel({ topic, minNotes }: { topic: TopicStats; minNotes: number }) {
  const maxMonth = Math.max(1, ...topic.notes_per_month.map((m) => m.notes));
  return (
    <aside
      data-testid="topic-panel"
      style={{ border: "1px solid #d0d7de", borderRadius: 8, padding: 16, background: "#fff", minWidth: 260, flex: "1 1 280px" }}
    >
      <h2 style={{ margin: "0 0 8px" }}>{topic.name}</h2>
      {topic.below_min_notes ? (
        <p style={{ color: "#9a6700" }}>Fewer than {minNotes} notes, so the trend is not shown on the map.</p>
      ) : null}
      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", margin: 0 }}>
        <dt>Notes</dt>
        <dd style={{ margin: 0 }}>{topic.note_count}</dd>
        <dt>Momentum</dt>
        <dd style={{ margin: 0 }}>{formatMomentum(topic.momentum)}</dd>
        <dt>Evergreens</dt>
        <dd style={{ margin: 0 }}>{topic.evergreens}</dd>
        <dt>Notes with links</dt>
        <dd style={{ margin: 0 }}>{topic.linked_notes}</dd>
        <dt>Top source</dt>
        <dd style={{ margin: 0 }}>{topic.top_source ? `${topic.top_source.name} (${topic.top_source.notes})` : "none"}</dd>
      </dl>
      <h3 style={{ fontSize: 14, marginBottom: 4 }}>Notes per month</h3>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 60 }} role="img" aria-label="Notes per month">
        {topic.notes_per_month.map((m) => (
          <div
            key={m.month}
            title={`${m.month}: ${m.notes}`}
            style={{ flex: 1, background: "#0969da", height: `${(m.notes / maxMonth) * 100}%`, minHeight: 2 }}
          />
        ))}
      </div>
      <p style={{ marginTop: 12 }}>
        <strong>Next step:</strong> {topic.next_step}
      </p>
      <Link href={`/topics/${topic.id}`} onClick={() => logUsage("action", "open_topic_page", topic.id)}>
        Open topic page
      </Link>
    </aside>
  );
}

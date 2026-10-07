import Link from "next/link";
import { formatMomentum, logUsage, type TopicStats, type Window } from "../lib/topicMap";
import { topicHref } from "../lib/topicPage";

export function TopicPanel({ topic, minNotes, window }: { topic: TopicStats; minNotes: number; window: Window }) {
  const maxMonth = Math.max(1, ...topic.notes_per_month.map((m) => m.notes));
  return (
    <aside data-testid="topic-panel" className="card">
      <h2>{topic.name}</h2>
      {topic.below_min_notes ? (
        <p className="warn">Fewer than {minNotes} notes, so the trend is not shown on the map.</p>
      ) : null}
      <dl className="facts">
        <dt>Notes</dt>
        <dd>{topic.note_count}</dd>
        <dt>Momentum</dt>
        <dd>{formatMomentum(topic.momentum)}</dd>
        <dt>Evergreens</dt>
        <dd>{topic.evergreens}</dd>
        <dt>Notes with links</dt>
        <dd>{topic.linked_notes}</dd>
        <dt>Top source</dt>
        <dd>{topic.top_source ? `${topic.top_source.name} (${topic.top_source.notes})` : "none"}</dd>
      </dl>
      <h3>Notes per month</h3>
      <div className="bars" role="img" aria-label="Notes per month">
        {topic.notes_per_month.map((m) => (
          <div key={m.month} title={`${m.month}: ${m.notes}`} style={{ height: `${(m.notes / maxMonth) * 100}%` }} />
        ))}
      </div>
      <p>
        <strong>Next step:</strong> {topic.next_step}
      </p>
      <Link href={topicHref(topic.id, window)} onClick={() => logUsage("action", "open_topic_page", topic.id)}>
        Open topic page
      </Link>
    </aside>
  );
}

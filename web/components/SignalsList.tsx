import Link from "next/link";
import { logUsage, type Window } from "../lib/topicMap";
import { signalHref, type Signal } from "../lib/topicPage";

export function SignalsList({ signals, window }: { signals: Signal[]; window: Window }) {
  return (
    <section aria-label="Signals">
      <h2 style={{ fontSize: 16, margin: "0 0 8px" }}>Signals</h2>
      {signals.length === 0 ? (
        <p data-testid="no-signals" style={{ margin: 0 }}>
          No signals right now.
        </p>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          {signals.map((s) => (
            <li key={`${s.topic_id}:${s.kind}`} data-testid="signal">
              <strong>{s.topic_name}:</strong> {s.message}{" "}
              <Link
                href={signalHref(s, window)}
                onClick={() => logUsage("action", "open_signal", `${s.topic_id}:${s.kind}`)}
              >
                See the {s.note_count} notes
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

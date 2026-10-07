import Link from "next/link";
import { logUsage, type Window } from "../lib/topicMap";
import { signalHref, type Signal } from "../lib/topicPage";

export function SignalsList({ signals, window }: { signals: Signal[]; window: Window }) {
  return (
    <section className="card" aria-label="Signals">
      <h2>Signals</h2>
      {signals.length === 0 ? (
        <p className="hint" data-testid="no-signals">
          No signals right now.
        </p>
      ) : (
        <ul className="signals">
          {signals.map((s) => (
            <li key={`${s.topic_id}:${s.kind}`} className="signal" data-testid="signal">
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

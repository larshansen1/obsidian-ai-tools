"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { formatMomentum, formatShare, logUsage, type Window } from "../lib/topicMap";
import {
  DUPLICATE_REASONS,
  fetchTopicPage,
  type NoteRef,
  type SignalKind,
  type TopicPage,
} from "../lib/topicPage";

const box: React.CSSProperties = { border: "1px solid #d0d7de", borderRadius: 8, padding: 16, background: "#fff" };

function NoteLine({ note }: { note: NoteRef }) {
  return (
    <>
      {note.title} <span style={{ color: "#57606a" }}>({note.created ?? "no date"})</span>
    </>
  );
}

export function TopicPageScreen({
  id,
  window,
  signal,
}: {
  id: string;
  window: Window;
  signal: SignalKind | null;
}) {
  const [data, setData] = useState<TopicPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    logUsage("screen_view", "topic_page", id);
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    fetchTopicPage(id, window, signal)
      .then((next) => {
        if (cancelled) return;
        setData(next);
        setError(null);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [id, window, signal]);

  const back = (
    <p>
      <Link href="/">Back to topic map</Link>
    </p>
  );
  if (error) {
    return (
      <main style={{ padding: 24 }}>
        {back}
        <p role="alert">{error}</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main style={{ padding: 24 }}>
        {back}
        <p>Loading…</p>
      </main>
    );
  }

  const { stats } = data;
  return (
    <main style={{ maxWidth: 1080, margin: "0 auto", padding: 24, display: "grid", gap: 16 }}>
      {back}
      <header style={box}>
        <h1 style={{ margin: "0 0 8px" }}>{stats.name}</h1>
        <dl data-testid="header" style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", margin: 0 }}>
          <dt>Notes</dt>
          <dd style={{ margin: 0 }}>{stats.note_count}</dd>
          <dt>Momentum</dt>
          <dd style={{ margin: 0 }}>{formatMomentum(stats.momentum)}</dd>
          <dt>Evergreens</dt>
          <dd style={{ margin: 0 }}>{stats.evergreens}</dd>
          <dt>Notes with links</dt>
          <dd style={{ margin: 0 }}>{stats.linked_notes}</dd>
          <dt>Tags</dt>
          <dd style={{ margin: 0 }}>{data.tags.join(", ")}</dd>
        </dl>
        {stats.below_min_notes ? (
          <p style={{ color: "#9a6700" }}>Fewer than {data.min_notes} notes, so the trend is not shown on the map.</p>
        ) : null}
      </header>

      {data.signal ? (
        <section style={box} aria-label="Signal notes" data-testid="signal-notes">
          <h2 style={{ margin: "0 0 8px", fontSize: 16 }}>Notes behind the signal</h2>
          <p style={{ marginTop: 0 }}>{data.signal.rules}</p>
          {data.signal.notes.length === 0 ? (
            <p>This signal no longer applies to the topic.</p>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {data.signal.notes.map((n) => (
                <li key={n.path}>
                  <NoteLine note={n} />
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <section style={box} aria-label="Sources">
        <h2 style={{ margin: "0 0 8px", fontSize: 16 }}>Notes per month</h2>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 60 }} role="img" aria-label="Notes per month">
          {stats.notes_per_month.map((m) => (
            <div
              key={m.month}
              title={`${m.month}: ${m.notes}`}
              style={{
                flex: 1,
                background: "#0969da",
                height: `${(m.notes / Math.max(1, ...stats.notes_per_month.map((x) => x.notes))) * 100}%`,
                minHeight: 2,
              }}
            />
          ))}
        </div>
        <h2 style={{ margin: "16px 0 8px", fontSize: 16 }}>Top sources</h2>
        <ul data-testid="top-sources" style={{ margin: 0, paddingLeft: 20 }}>
          {data.top_sources.map((s) => (
            <li key={s.name}>
              {s.name} ({s.notes})
            </li>
          ))}
          <li>Unknown source ({data.unknown_source_notes})</li>
        </ul>
        <h2 style={{ margin: "16px 0 8px", fontSize: 16 }}>By source type</h2>
        <ul data-testid="source-types" style={{ margin: 0, paddingLeft: 20 }}>
          {data.source_types.map((t) => (
            <li key={t.source_type}>
              {t.source_type}: {t.notes} ({formatShare(t.share)})
            </li>
          ))}
        </ul>
      </section>

      <section style={box} aria-label="Evergreens">
        <h2 style={{ margin: "0 0 8px", fontSize: 16 }}>Evergreens and hypotheses</h2>
        {data.evergreens.length === 0 ? (
          <p style={{ margin: 0 }}>None yet.</p>
        ) : (
          <ul data-testid="evergreens" style={{ margin: 0, paddingLeft: 20 }}>
            {data.evergreens.map((n) => (
              <li key={n.path}>
                <NoteLine note={n} />
                {n.bridges.length > 0 ? (
                  <span> bridges to {n.bridges.map((b) => b.name).join(", ")}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section style={box} aria-label="Recent notes">
        <h2 style={{ margin: "0 0 8px", fontSize: 16 }}>Recent notes</h2>
        <ul data-testid="recent-notes" style={{ margin: 0, paddingLeft: 20 }}>
          {data.recent_notes.map((n) => (
            <li key={n.path}>
              <NoteLine note={n} />
              {n.duplicates.length > 0 ? (
                <span style={{ color: "#9a6700" }}>
                  {" "}
                  Likely duplicate of {n.duplicates.map((d) => `${d.path} (${DUPLICATE_REASONS[d.reason] ?? d.reason})`).join(", ")}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

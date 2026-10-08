"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ClaimsSection } from "./ClaimsSection";
import { CoverageSection } from "./CoverageSection";
import { formatMomentum, formatShare, logUsage, type Window } from "../lib/topicMap";
import {
  DUPLICATE_REASONS,
  fetchTopicPage,
  type NoteRef,
  type SignalKind,
  type TopicPage,
} from "../lib/topicPage";

function NoteLine({ note }: { note: NoteRef }) {
  return (
    <>
      {note.title} <span className="muted">({note.created ?? "no date"})</span>
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
    <p className="back">
      <Link href="/">Back to topic map</Link>
    </p>
  );
  if (error) {
    return (
      <main className="page">
        {back}
        <p role="alert">{error}</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="page">
        {back}
        <p>Loading…</p>
      </main>
    );
  }

  const { stats } = data;
  return (
    <main className="page">
      {back}
      <header className="card">
        <h1 className="page-title" style={{ marginBottom: 12 }}>{stats.name}</h1>
        <dl data-testid="header" className="facts">
          <dt>Notes</dt>
          <dd>{stats.note_count}</dd>
          <dt>Momentum</dt>
          <dd>{formatMomentum(stats.momentum)}</dd>
          <dt>Evergreens</dt>
          <dd>{stats.evergreens}</dd>
          <dt>Notes with links</dt>
          <dd>{stats.linked_notes}</dd>
          <dt>Tags</dt>
          <dd>{data.tags.join(", ")}</dd>
        </dl>
        {stats.below_min_notes ? (
          <p className="warn">Fewer than {data.min_notes} notes, so the trend is not shown on the map.</p>
        ) : null}
      </header>

      {data.signal ? (
        <section className="card" aria-label="Signal notes" data-testid="signal-notes">
          <h2>Notes behind the signal</h2>
          <p className="muted" style={{ marginTop: 0 }}>{data.signal.rules}</p>
          {data.signal.notes.length === 0 ? (
            <p>This signal no longer applies to the topic.</p>
          ) : (
            <ul className="list">
              {data.signal.notes.map((n) => (
                <li key={n.path}>
                  <NoteLine note={n} />
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <section className="card" aria-label="Sources">
        <h2>Notes per month</h2>
        <div className="bars" role="img" aria-label="Notes per month">
          {stats.notes_per_month.map((m) => (
            <div
              key={m.month}
              title={`${m.month}: ${m.notes}`}
              style={{ height: `${(m.notes / Math.max(1, ...stats.notes_per_month.map((x) => x.notes))) * 100}%` }}
            />
          ))}
        </div>
        <h3>Top sources</h3>
        <ul data-testid="top-sources" className="list">
          {data.top_sources.map((s) => (
            <li key={s.name}>
              {s.name} ({s.notes})
            </li>
          ))}
          <li>Unknown source ({data.unknown_source_notes})</li>
        </ul>
        <h3>By source type</h3>
        <ul data-testid="source-types" className="list">
          {data.source_types.map((t) => (
            <li key={t.source_type}>
              {t.source_type}: {t.notes} ({formatShare(t.share)})
            </li>
          ))}
        </ul>
      </section>

      <ClaimsSection topic={id} />

      <CoverageSection topic={id} />

      <section className="card" aria-label="Evergreens">
        <h2>Evergreens and hypotheses</h2>
        {data.evergreens.length === 0 ? (
          <p className="hint">None yet.</p>
        ) : (
          <ul data-testid="evergreens" className="list">
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

      <section className="card" aria-label="Recent notes">
        <h2>Recent notes</h2>
        <ul data-testid="recent-notes" className="list">
          {data.recent_notes.map((n) => (
            <li key={n.path}>
              <NoteLine note={n} />
              {n.duplicates.length > 0 ? (
                <span className="dup">
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

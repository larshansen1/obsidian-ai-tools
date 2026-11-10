"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { fetchAiStatus, noteUrl } from "../lib/chat";
import { ClaimsSection } from "./ClaimsSection";
import { CoverageSection } from "./CoverageSection";
import { EditTopic, TopicNoteAction } from "./WriteActions";
import { formatMomentum, formatShare, logUsage, type Window } from "../lib/topicMap";
import {
  DUPLICATE_REASONS,
  fetchTopicPage,
  type NoteRef,
  type SignalKind,
  type TopicPage,
} from "../lib/topicPage";

// The title opens the note in Obsidian once the vault's name is known.
function NoteLine({ note, vault }: { note: NoteRef; vault: string }) {
  return (
    <>
      {vault ? <a href={noteUrl(vault, note.path)}>{note.title}</a> : note.title}{" "}
      <span className="muted">({note.created ?? "no date"})</span>
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
  // Bumped after a write so the page re-reads the refreshed data.
  const [version, setVersion] = useState(0);
  // True once the claims card leads with an answered question (#171).
  const [leading, setLeading] = useState(false);
  const onWrite = useCallback(() => setVersion((v) => v + 1), []);
  const [vault, setVault] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchAiStatus()
      .then((status) => {
        if (!cancelled) setVault(status.vault_name);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

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
  }, [id, window, signal, version]);

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
  // Signal notes and sources. Once a question is answered the claims card moves above them (#171).
  const overview = (
    <>
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
                  <NoteLine note={n} vault={vault} />
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
    </>
  );
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
          <dd>
            {/* Folded: twelve tags wrap into many lines and push the answer off a phone screen (#171). */}
            <details>
              <summary>{data.tags.length}</summary>
              {data.tags.join(", ")}
            </details>
          </dd>
        </dl>
        <EditTopic topic={id} tags={data.tags} onChange={() => setVersion((v) => v + 1)} />
        {stats.below_min_notes ? (
          <p className="warn">Fewer than {data.min_notes} notes, so the trend is not shown on the map.</p>
        ) : null}
      </header>

      {/* The claims card keeps its place in the tree (moving it would remount it mid-run);
          the overview moves around it instead. */}
      {leading ? null : overview}
      <ClaimsSection topic={id} onWrite={onWrite} onAnswerFirst={setLeading} />
      {leading ? overview : null}


      <CoverageSection topic={id} />

      <section className="card" aria-label="Evergreens">
        <h2>Evergreens and hypotheses</h2>
        {data.evergreens.length === 0 ? (
          <p className="hint">None yet.</p>
        ) : (
          <ul data-testid="evergreens" className="list">
            {data.evergreens.map((n) => (
              <li key={n.path}>
                <NoteLine note={n} vault={vault} />
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
              <NoteLine note={n} vault={vault} />
              <span className="muted"> · {n.matched_tags.length === 1 ? "tag" : "tags"}: {n.matched_tags.join(", ")}</span>
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

      {data.single_tag_notes.length > 0 || data.excluded_notes.length > 0 ? (
        <section className="card" aria-label="Check what belongs" data-testid="belongs">
          <h2>Check what belongs</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            A note is in this topic when it carries one of the topic’s tags. A broad tag can pull in notes about
            something else. Take those out here; the tag and the note itself stay as they are.
          </p>
          {data.single_tag_notes.length > 0 ? (
            <details data-testid="single-tag-notes">
              <summary>In this topic through one tag only ({data.single_tag_notes.length})</summary>
              <ul className="list">
                {data.single_tag_notes.map((n) => (
                  <li key={n.path} className="belongs-row">
                    <span>
                      <NoteLine note={n} vault={vault} />
                      <span className="muted"> · tag: {n.matched_tags.join(", ")}</span>
                    </span>{" "}
                    <TopicNoteAction topic={id} path={n.path} title={n.title} mode="exclude" onChange={onWrite} />
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {data.excluded_notes.length > 0 ? (
            <details data-testid="excluded-notes">
              <summary>Taken out of this topic ({data.excluded_notes.length})</summary>
              <ul className="list">
                {data.excluded_notes.map((n) => (
                  <li key={n.path} className="belongs-row">
                    <span>
                      <NoteLine note={n} vault={vault} />
                    </span>{" "}
                    <TopicNoteAction topic={id} path={n.path} title={n.title} mode="restore" onChange={onWrite} />
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}

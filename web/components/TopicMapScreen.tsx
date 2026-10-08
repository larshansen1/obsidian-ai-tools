"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BubbleChart } from "./BubbleChart";
import { SummaryTiles } from "./SummaryTiles";
import { SignalsList } from "./SignalsList";
import { TopicPanel } from "./TopicPanel";
import { WINDOWS, fetchTopicMap, logUsage, type TopicMap, type Window } from "../lib/topicMap";

export function TopicMapScreen() {
  const [window, setWindow] = useState<Window>("30");
  const [data, setData] = useState<TopicMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    logUsage("screen_view", "topic_map");
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchTopicMap(window)
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
  }, [window]);

  if (error) {
    return (
      <main className="page">
        <p role="alert">{error}</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="page">
        <p>Loading…</p>
      </main>
    );
  }

  const selected = data.topics.find((t) => t.id === selectedId) ?? null;
  return (
    <main className="page">
      <h1 className="page-title">Topic map</h1>
      <p className="muted">
        <Link href="/changes">Recent changes</Link>
      </p>
      <SummaryTiles tiles={data.tiles} />
      <SignalsList signals={data.signals} window={window} />
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Window">
        {WINDOWS.map((w) => (
          <button
            key={w.value}
            aria-pressed={w.value === window}
            onClick={() => {
              setWindow(w.value);
              logUsage("action", "change_window", w.value);
            }}
          >
            {w.label}
          </button>
        ))}
        </div>
        <details className="explain">
          <summary>How momentum is calculated</summary>
          <p>{data.momentum_formula}</p>
        </details>
      </div>
      <div className="layout">
        <div className="card">
          <BubbleChart
            topics={data.topics}
            selectedId={selectedId}
            onSelect={(id) => {
              setSelectedId(id);
              logUsage("action", "select_topic", id);
            }}
          />
          <p className="legend" data-testid="legend">
            <strong>Up: evergreens.</strong> An evergreen is a note of your own thinking, kept in the
            notes/evergreen folder (hypotheses count too). A topic counts the evergreens that carry its tags. More
            evergreens means you have worked the topic through. Links are shown separately. <strong>Across:</strong>{" "}
            momentum. <strong>Size:</strong> number of notes.
          </p>
        </div>
        {selected ? (
          <TopicPanel topic={selected} minNotes={data.min_notes} window={window} />
        ) : (
          <p className="hint card">Click a bubble to see the topic.</p>
        )}
      </div>
    </main>
  );
}

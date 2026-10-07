"use client";

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
      <main style={{ padding: 24 }}>
        <p role="alert">{error}</p>
      </main>
    );
  }
  if (!data) {
    return (
      <main style={{ padding: 24 }}>
        <p>Loading…</p>
      </main>
    );
  }

  const selected = data.topics.find((t) => t.id === selectedId) ?? null;
  return (
    <main style={{ maxWidth: 1080, margin: "0 auto", padding: 24, display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Topic map</h1>
      <SummaryTiles tiles={data.tiles} />
      <SignalsList signals={data.signals} window={window} />
      <div style={{ display: "flex", gap: 8, alignItems: "center" }} role="group" aria-label="Window">
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
        <details style={{ marginLeft: 8 }}>
          <summary>How momentum is calculated</summary>
          <p style={{ maxWidth: 560 }}>{data.momentum_formula}</p>
        </details>
      </div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 480px" }}>
          <BubbleChart
            topics={data.topics}
            selectedId={selectedId}
            onSelect={(id) => {
              setSelectedId(id);
              logUsage("action", "select_topic", id);
            }}
          />
        </div>
        {selected ? (
          <TopicPanel topic={selected} minNotes={data.min_notes} window={window} />
        ) : (
          <p style={{ flex: "1 1 280px" }}>Click a bubble to see the topic.</p>
        )}
      </div>
    </main>
  );
}

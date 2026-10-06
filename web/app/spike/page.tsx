"use client";

import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  makeAssistantToolUI,
} from "@assistant-ui/react";
import { useDataStreamRuntime } from "@assistant-ui/react-data-stream";

type TopicStatsArgs = { topic: string };
type TopicStatsResult = { topic: string; notes: number; evergreens: number; momentum: number };

const card: React.CSSProperties = {
  border: "1px solid #ccc",
  borderRadius: 8,
  padding: 12,
  margin: "8px 0",
  background: "#fafafa",
};

const TopicStatsUI = makeAssistantToolUI<TopicStatsArgs, TopicStatsResult>({
  toolName: "topic_stats",
  render: ({ args, result }) => (
    <div style={card} data-testid="topic-stats-card">
      <strong>Topic stats: {args.topic}</strong>
      {result ? (
        <div>
          {result.notes} notes · {result.evergreens} evergreens · momentum {result.momentum}
        </div>
      ) : (
        <div>Loading…</div>
      )}
    </div>
  ),
});

type FakeWriteArgs = { path: string; change: string };
type FakeWriteResult = { approved: boolean };

const FakeWriteUI = makeAssistantToolUI<FakeWriteArgs, FakeWriteResult>({
  toolName: "fake_write",
  display: "standalone",
  render: ({ args, result, addResult }) => (
    <div style={card} data-testid="fake-write-card">
      <strong>Write to {args.path}</strong>
      <div>{args.change}</div>
      {result === undefined ? (
        <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
          <button onClick={() => addResult({ approved: true })}>Approve</button>
          <button onClick={() => addResult({ approved: false })}>Reject</button>
        </div>
      ) : (
        <div>{result.approved ? "Approved" : "Rejected"}</div>
      )}
    </div>
  ),
});

const UserMessage = () => (
  <MessagePrimitive.Root style={{ textAlign: "right", margin: "8px 0" }}>
    <MessagePrimitive.Parts />
  </MessagePrimitive.Root>
);

const AssistantMessage = () => (
  <MessagePrimitive.Root style={{ margin: "8px 0" }}>
    <MessagePrimitive.Parts />
  </MessagePrimitive.Root>
);

export default function Page() {
  const runtime = useDataStreamRuntime({
    api: "/api/chat",
    // Without this the run would not resume after addResult on a backend tool.
    unstable_humanToolNames: ["fake_write"],
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <TopicStatsUI />
      <FakeWriteUI />
      <ThreadPrimitive.Root style={{ maxWidth: 640, margin: "0 auto", padding: 16 }}>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
        </ThreadPrimitive.Viewport>
        <ComposerPrimitive.Root style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <ComposerPrimitive.Input placeholder="Ask about a topic, or say 'write'" style={{ flex: 1 }} />
          <ComposerPrimitive.Send>Send</ComposerPrimitive.Send>
        </ComposerPrimitive.Root>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}

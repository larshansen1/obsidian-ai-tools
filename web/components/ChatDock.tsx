"use client";

import {
  AssistantRuntimeProvider,
  useAui,
  useAuiState,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  makeAssistantToolUI,
} from "@assistant-ui/react";
import { useDataStreamRuntime } from "@assistant-ui/react-data-stream";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import {
  CONFIRM_TOOL,
  fetchAiStatus,
  formatUsd,
  loadHistory,
  noteLabel,
  noteUrl,
  saveHistory,
  scopeFor,
  splitCitations,
  type AiStatus,
} from "../lib/chat";

const VaultName = createContext("");

function CitedText({ text }: { text: string }) {
  const vault = useContext(VaultName);
  return (
    <p className="chat-text">
      {splitCitations(text).map((part, i) =>
        part.kind === "text" ? (
          <span key={i}>{part.text}</span>
        ) : (
          <a key={i} className="chat-cite" href={noteUrl(vault, part.path)} title={part.path}>
            {noteLabel(part.path)}
          </a>
        ),
      )}
    </p>
  );
}

type NoteHit = { path: string; title: string };

function ToolCard({ toolName, result }: { toolName: string; result?: unknown }) {
  const vault = useContext(VaultName);
  if (result === undefined) return <div className="chat-tool muted">Running {toolName}…</div>;
  const error = (result as { error?: string } | null)?.error;
  if (error) return <div className="chat-tool muted">{error}</div>;
  if (toolName === "search_notes" && Array.isArray(result)) {
    const hits = result as NoteHit[];
    return (
      <div className="chat-tool">
        <strong>
          Found {hits.length} {hits.length === 1 ? "note" : "notes"}
        </strong>
        <ul>
          {hits.map((hit) => (
            <li key={hit.path}>
              <a href={noteUrl(vault, hit.path)}>{hit.title}</a>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (toolName === "read_note") {
    const note = result as NoteHit;
    return (
      <div className="chat-tool">
        Read <a href={noteUrl(vault, note.path)}>{note.title}</a>
      </div>
    );
  }
  if (toolName === "topic_stats") {
    const stats = result as { name: string; note_count: number; evergreens: number };
    return (
      <div className="chat-tool">
        <strong>{stats.name}</strong>: {stats.note_count} notes, {stats.evergreens} evergreens
      </div>
    );
  }
  return <div className="chat-tool muted">{toolName} done</div>;
}

const CostConfirmUI = makeAssistantToolUI<
  { reason: string; estimate_usd: number },
  { approved: boolean }
>({
  toolName: CONFIRM_TOOL,
  display: "standalone",
  render: ({ args, result, addResult }) => (
    <div className="chat-tool chat-confirm" data-testid="cost-confirm">
      <strong>This costs more than your limit</strong>
      <div>{args.reason}</div>
      {result === undefined ? (
        <div className="chat-actions">
          <button onClick={() => addResult({ approved: true })}>Run anyway</button>
          <button onClick={() => addResult({ approved: false })}>Cancel</button>
        </div>
      ) : (
        <div className="muted">{result.approved ? "Approved" : "Cancelled"}</div>
      )}
    </div>
  ),
});

function UserMessage() {
  return (
    <MessagePrimitive.Root className="chat-msg chat-user">
      <MessagePrimitive.Parts components={{ Text: CitedText }} />
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="chat-msg chat-assistant">
      <MessagePrimitive.Parts
        components={{
          Text: CitedText,
          tools: { Fallback: ({ toolName, result }) => <ToolCard toolName={toolName} result={result} /> },
        }}
      />
    </MessagePrimitive.Root>
  );
}

function SpendLine({ status }: { status: AiStatus | null }) {
  if (!status) return null;
  return (
    <div className="chat-spend muted">
      {status.configured ? status.model : "No OpenRouter key set"} · this month{" "}
      {formatUsd(status.month_spend_usd)} of {formatUsd(status.monthly_limit_usd)}
    </div>
  );
}

// Renders nothing. Lives inside the provider so the thread is attached before it is used.
function ThreadPersistence({
  topic,
  open,
  onStatus,
}: {
  topic: string | null;
  open: boolean;
  onStatus: (status: AiStatus) => void;
}) {
  const aui = useAui();
  const messages = useAuiState((s) => s.thread.messages);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  // The main thread is a locked placeholder until the runtime has attached it.
  const isLoading = useAuiState((s) => s.thread.isLoading);
  const loaded = useRef(false);

  // One conversation per topic (C9): load it when the topic changes...
  useEffect(() => {
    if (isLoading) return;
    let cancelled = false;
    loaded.current = false;
    aui.thread().reset();
    loadHistory(topic)
      .catch(() => null)
      .then((saved) => {
        if (cancelled) return;
        if (saved) aui.thread().import(saved as Parameters<ReturnType<typeof aui.thread>["import"]>[0]);
        loaded.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, [aui, topic, isLoading]);

  // ...and save it whenever a reply is finished, not while it is still streaming.
  useEffect(() => {
    if (!loaded.current || isRunning) return;
    void saveHistory(topic, aui.thread().export()).catch(() => undefined);
  }, [aui, topic, messages, isRunning]);

  // Monthly spend: refresh when the panel opens and after each reply finishes.
  useEffect(() => {
    if (!open || isRunning) return;
    fetchAiStatus()
      .then(onStatus)
      .catch(() => undefined);
  }, [open, isRunning, onStatus]);

  return null;
}

export function ChatDock({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const scope = scopeFor(pathname);
  const scopeRef = useRef(scope);
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<AiStatus | null>(null);

  useEffect(() => {
    scopeRef.current = scope;
  });

  // Every message carries the current screen and topic (C3).
  const runtime = useDataStreamRuntime({
    api: "/api/chat",
    body: async () => ({ screen: scopeRef.current.screen, topic: scopeRef.current.topic }),
    // The cost question waits for a click, like any write (spike finding 4).
    unstable_humanToolNames: [CONFIRM_TOOL],
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <CostConfirmUI />
      <ThreadPersistence topic={scope.topic} open={open} onStatus={setStatus} />
      <VaultName.Provider value={status?.vault_name ?? ""}>
        <div className={open ? "dock dock-open" : "dock"}>
          <div className="dock-main">{children}</div>
          <aside className="dock-panel" hidden={!open} aria-label="Chat">
            <ThreadPrimitive.Root className="chat-thread">
              <SpendLine status={status} />
              <ThreadPrimitive.Viewport className="chat-viewport">
                <ThreadPrimitive.Empty>
                  <p className="muted">
                    {scope.topic
                      ? "Ask about this topic, for example: what am I missing?"
                      : "Ask about your vault."}
                  </p>
                </ThreadPrimitive.Empty>
                <ThreadPrimitive.Messages>
                  {({ message }) => (message.role === "user" ? <UserMessage /> : <AssistantMessage />)}
                </ThreadPrimitive.Messages>
              </ThreadPrimitive.Viewport>
              <ComposerPrimitive.Root className="chat-composer">
                <ComposerPrimitive.Input placeholder="Ask about your notes" rows={1} />
                <ComposerPrimitive.Send>Send</ComposerPrimitive.Send>
              </ComposerPrimitive.Root>
            </ThreadPrimitive.Root>
          </aside>
        </div>
      </VaultName.Provider>
      <button
        type="button"
        className={open ? "chat-toggle chat-toggle-open" : "chat-toggle"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "Close chat" : "Chat"}
      </button>
    </AssistantRuntimeProvider>
  );
}

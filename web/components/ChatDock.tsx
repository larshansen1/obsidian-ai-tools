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
import { Agreement, ClaimsTable } from "./ClaimsView";
import type { ClaimsView } from "../lib/claims";
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
type TagCount = { tag: string; notes: number };
type CoverageGap = {
  url: string;
  title: string;
  type: string;
  from_citations: boolean;
  note_path?: string;
};
type CoverageGapsResult = {
  gaps: CoverageGap[];
  count: number;
  by_type: { [key: string]: number };
};
type SourceCandidate = CoverageGap;
type SourceCandidatesResult = {
  candidates: SourceCandidate[];
  count: number;
  sources: { citations: number; web: number };
};

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

// One quiet line per tool call; open it to see the details.
function Folded({ summary, children }: { summary: string; children?: ReactNode }) {
  if (!children) return <div className="chat-tool chat-tool-line muted">{summary}</div>;
  return (
    <details className="chat-tool chat-tool-line">
      <summary className="muted">{summary}</summary>
      {children}
    </details>
  );
}

export function ToolCard({
  toolName,
  args,
  result,
}: {
  toolName: string;
  args?: unknown;
  result?: unknown;
}) {
  const vault = useContext(VaultName);
  if (result === undefined) return <Folded summary={`Running ${toolName}…`} />;
  const error = (result as { error?: string } | null)?.error;
  if (error) return <Folded summary={error} />;
  if (toolName === "search_notes" && Array.isArray(result)) {
    const hits = result as NoteHit[];
    const query = (args as { query?: string } | undefined)?.query ?? "";
    return (
      <Folded summary={`Searched for "${query}": ${plural(hits.length, "note")}`}>
        <ul>
          {hits.map((hit) => (
            <li key={hit.path}>
              <a href={noteUrl(vault, hit.path)}>{hit.title}</a>
            </li>
          ))}
        </ul>
      </Folded>
    );
  }
  if (toolName === "read_note") {
    const note = result as NoteHit;
    return (
      <Folded summary="Read a note">
        <a href={noteUrl(vault, note.path)}>{note.title}</a>
      </Folded>
    );
  }
  if (toolName === "topic_tags" && Array.isArray(result)) {
    const tags = result as TagCount[];
    return (
      <Folded summary={`Looked at ${plural(tags.length, "tag")} on the topic`}>
        <p className="chat-text">{tags.map((t) => `${t.tag} (${t.notes})`).join(", ")}</p>
      </Folded>
    );
  }
  if (toolName === "topic_claims") {
    const view = result as ClaimsView;
    return (
      <Folded summary={`Claims on the topic: ${plural(view.claim_count, "claim")}`}>
        <ClaimsTable view={view} vault={vault} />
        <Agreement view={view} vault={vault} />
      </Folded>
    );
  }
  if (toolName === "topic_stats") {
    const stats = result as { name: string; note_count: number; evergreens: number };
    return (
      <Folded summary={`${stats.name}: ${plural(stats.note_count, "note")}, ${plural(stats.evergreens, "evergreen")}`} />
    );
  }
  if (toolName === "coverage_gaps") {
    const gaps = result as CoverageGapsResult;
    return (
      <Folded
        summary={`Coverage gaps: ${plural(gaps.count, "source")} cited but not ingested`}
      >
        <div className="chat-coverage-gaps">
          {Object.entries(gaps.by_type).map(([type, count]) => (
            <div key={type} className="chat-gap-bar">
              <span className="gap-type">{type}</span>
              <span className="gap-count">{count}</span>
            </div>
          ))}
        </div>
      </Folded>
    );
  }
  if (toolName === "source_candidates") {
    const candidates = result as SourceCandidatesResult;
    return (
      <Folded
        summary={`Source candidates: ${plural(candidates.count, "source")} to fill gaps`}
      >
        <ul className="chat-candidates">
          {candidates.candidates.map((source) => (
            <li key={source.url} className="chat-candidate">
              <div className="candidate-header">
                <a href={source.url} target="_blank" rel="noopener noreferrer">
                  {source.title}
                </a>
                <span className="candidate-type">{source.type}</span>
              </div>
              {source.note_path && (
                <div className="candidate-from muted">
                  From: <a href={noteUrl(vault, source.note_path)}>{noteLabel(source.note_path)}</a>
                </div>
              )}
            </li>
          ))}
        </ul>
      </Folded>
    );
  }
  return <Folded summary={`${toolName} done`} />;
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
          tools: {
            Fallback: ({ toolName, args, result }) => (
              <ToolCard toolName={toolName} args={args} result={result} />
            ),
          },
        }}
      />
    </MessagePrimitive.Root>
  );
}

// Shown from the moment you send until the reply is finished (including cost questions
// and tool calls), so a slow model never looks like a frozen panel.
export function WorkingIndicator({ running }: { running: boolean }) {
  if (!running) return null;
  return (
    <div className="chat-working muted" role="status">
      Working<span className="chat-dots" aria-hidden="true" />
    </div>
  );
}

function ThreadWorking() {
  const running = useAuiState((s) => s.thread.isRunning);
  return <WorkingIndicator running={running} />;
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
                <ThreadWorking />
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

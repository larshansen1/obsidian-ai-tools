"use client";

import { useEffect, useState } from "react";
import { fetchAiStatus } from "../lib/chat";
import {
  fetchClaims,
  progressText,
  runClaims,
  runLabel,
  saveQuestion,
  type ClaimsRun,
  type ClaimsView,
} from "../lib/claims";
import { logUsage } from "../lib/topicMap";
import { Agreement, ClaimsTable } from "./ClaimsView";

type Notice = { kind: "approval" | "error"; message: string };

function noticeFor(run: ClaimsRun): Notice | null {
  if (run.status === "done") return null;
  const message = run.message ?? "The run stopped.";
  if (run.status === "needs_approval") {
    return { kind: "approval", message: `${message} Estimated next call: $${(run.estimate_usd ?? 0).toFixed(4)}.` };
  }
  return { kind: "error", message };
}

export function ClaimsSection({ topic }: { topic: string }) {
  const [view, setView] = useState<ClaimsView | null>(null);
  const [vault, setVault] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [custom, setCustom] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchClaims(topic)
      .then((next) => {
        if (!cancelled) setView(next);
      })
      .catch((e: Error) => {
        if (!cancelled) setNotice({ kind: "error", message: e.message });
      });
    fetchAiStatus()
      .then((status) => {
        if (!cancelled) setVault(status.vault_name);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [topic]);

  async function run(approved: boolean) {
    setBusy(true);
    setNotice(null);
    try {
      const result = await runClaims(topic, approved);
      setView(result.view);
      setNotice(noticeFor(result));
    } catch (e) {
      setNotice({ kind: "error", message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function choose(question: string) {
    const text = question.trim();
    if (!text) return;
    logUsage("action", "claims_question", topic);
    try {
      setView(await saveQuestion(topic, text));
    } catch (e) {
      setNotice({ kind: "error", message: (e as Error).message });
      return;
    }
    await run(false);
  }

  return (
    <section className="card" aria-label="Claims across sources" data-testid="claims">
      <h2>Claims across sources</h2>
      {view ? <p className="muted">{progressText(view)}</p> : null}
      {view && (view.stale || view.claim_count === 0) ? (
        <button type="button" disabled={busy} onClick={() => void run(false)}>
          {busy ? "Working…" : runLabel(view)}
        </button>
      ) : null}
      {notice ? (
        <div role={notice.kind === "error" ? "alert" : "status"} className="warn">
          {notice.message}
          {notice.kind === "approval" ? (
            <button type="button" disabled={busy} onClick={() => void run(true)}>
              Run anyway
            </button>
          ) : null}
        </div>
      ) : null}
      {view && view.claim_count > 0 ? (
        <>
          <h3>Question</h3>
          <ul className="list" data-testid="questions">
            {view.questions.map((q) => (
              <li key={q}>
                {q}{" "}
                <button type="button" disabled={busy} onClick={() => void choose(q)}>
                  {q === view.question ? "Chosen" : "Use this question"}
                </button>
              </li>
            ))}
          </ul>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void choose(custom || view.question || "");
            }}
          >
            <input
              aria-label="Your own question"
              value={custom}
              placeholder={view.question ?? "Write your own question"}
              onChange={(event) => setCustom(event.target.value)}
            />
            <button type="submit" disabled={busy}>
              Use my question
            </button>
          </form>
          <ClaimsTable view={view} vault={vault} />
          <h3>Where they agree</h3>
          <Agreement view={view} vault={vault} />
        </>
      ) : null}
    </section>
  );
}

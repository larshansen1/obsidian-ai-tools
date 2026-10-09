"use client";

import { useEffect, useState, type ReactNode } from "react";
import { fetchAiStatus } from "../lib/chat";
import {
  fetchClaims,
  progressText,
  runClaims,
  runLabel,
  saveQuestion,
  stepStates,
  type ClaimsRun,
  type ClaimsView,
  type StepState,
} from "../lib/claims";
import { logUsage } from "../lib/topicMap";
import { Agreement, ClaimsTable, type ClaimSelection } from "./ClaimsView";
import { DraftEvergreen, LinkNotes } from "./WriteActions";

type Notice = { kind: "approval" | "error"; message: string };

const BADGES: Record<StepState, string> = { done: "Done", next: "Do this next", waiting: "Waiting" };

function noticeFor(run: ClaimsRun): Notice | null {
  if (run.status === "done") return null;
  const message = run.message ?? "The run stopped.";
  if (run.status === "needs_approval") {
    return { kind: "approval", message: `${message} Estimated next call: $${(run.estimate_usd ?? 0).toFixed(4)}.` };
  }
  return { kind: "error", message };
}

function Step({
  number,
  title,
  state,
  children,
}: {
  number: number;
  title: string;
  state: StepState;
  children: ReactNode;
}) {
  return (
    <section className="step" aria-label={title} data-state={state}>
      <h3 className="step-title">
        <span className="step-num">{number}</span>
        {title}
        <span className={`step-badge step-${state}`}>{BADGES[state]}</span>
      </h3>
      {children}
    </section>
  );
}

// `onWrite` hears about a saved or undone evergreen, so the page can re-read its evergreens.
export function ClaimsSection({ topic, onWrite }: { topic: string; onWrite?: () => void }) {
  const [view, setView] = useState<ClaimsView | null>(null);
  const [vault, setVault] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [custom, setCustom] = useState("");
  // Bumped after a link write so the unlinked notes are read again.
  const [version, setVersion] = useState(0);
  // Claims ticked for "Draft new evergreen", in the order they were ticked.
  const [picked, setPicked] = useState<string[]>([]);

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
  }, [topic, version]);

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
    }
  }

  if (!view) {
    return (
      <section className="card" aria-label="Claims across sources" data-testid="claims">
        <h2>Claims across sources</h2>
        {notice ? <p role="alert" className="warn">{notice.message}</p> : <p className="muted">Loading…</p>}
      </section>
    );
  }

  // Only claims on screen count: a re-sort or another topic can drop ticked ones.
  const shown = new Set(
    [...view.supporting, ...view.pushing_back, ...view.shared.flatMap((s) => s.claims)].map((c) => c.id),
  );
  const live = picked.filter((id) => shown.has(id));
  const selection: ClaimSelection = {
    selected: new Set(live),
    toggle: (ids) =>
      setPicked((current) =>
        ids.every((id) => current.includes(id))
          ? current.filter((id) => !ids.includes(id))
          : [...current, ...ids.filter((id) => !current.includes(id))],
      ),
  };
  const afterDraft = () => {
    setPicked([]);
    setVersion((v) => v + 1);
    onWrite?.();
  };

  const states = stepStates(view);
  const label = runLabel(view);
  const proposals = view.questions;

  // The one button for whichever step is next, with the run's message right under it.
  const action = (step: ClaimsView["next_step"]) =>
    view.next_step === step && label ? (
      <div className="step-action">
        <button type="button" className="primary" disabled={busy} onClick={() => void run(false)}>
          {busy ? "Working…" : label}
        </button>
        {busy ? <span className="muted"> This can take a minute.</span> : null}
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
      </div>
    ) : null;

  return (
    <section className="card" aria-label="Claims across sources" data-testid="claims">
      <h2>Claims across sources</h2>
      <p className="muted">
        What your notes claim, how they split on one question, and where several notes agree. Work through the
        steps in order.
      </p>
      {view.next_step === "done" && notice ? (
        <div role={notice.kind === "error" ? "alert" : "status"} className="warn">
          {notice.message}
        </div>
      ) : null}

      <Step number={1} title="Read your notes" state={states.read}>
        <p className="step-status">{progressText(view)}</p>
        {action("read")}
      </Step>

      <Step number={2} title="Choose a question" state={states.question}>
        {states.question === "waiting" ? (
          <p className="hint">Read your notes first. Then the AI suggests questions your claims bear on.</p>
        ) : (
          <>
            <p className="step-status">
              {view.question ? "Question chosen. You can switch to another at any time." : "Pick the yes or no question to sort your claims by."}
            </p>
            {proposals.length === 0 ? <p className="hint">No suggestions yet.</p> : null}
            <ul className="list" data-testid="questions">
              {proposals.map((q) => (
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
              <label>
                Or write your own question{" "}
                <input
                  aria-label="Your own question"
                  value={custom}
                  placeholder={view.question ?? "Is …?"}
                  onChange={(event) => setCustom(event.target.value)}
                />
              </label>{" "}
              <button type="submit" disabled={busy}>
                Use my question
              </button>
            </form>
            {action("analyse")}
          </>
        )}
      </Step>

      <Step number={3} title="Claims for and against" state={states.sides}>
        {view.question === null ? (
          <p className="hint">Choose a question in step 2. Each claim is then placed on one side.</p>
        ) : view.next_step === "sort" ? (
          <p className="step-status">
            The question is chosen: “{view.question}” Now place the {view.claim_count} claims on a side.
          </p>
        ) : states.sides === "waiting" ? (
          <p className="hint">Finish the steps above first.</p>
        ) : (
          <ClaimsTable view={view} vault={vault} selection={selection} />
        )}
        {action("sort")}
      </Step>

      <Step number={4} title="Where they agree" state={states.agree}>
        {states.agree === "waiting" ? (
          <p className="hint">Needs step 2 first. It finds claims several notes make that match your evergreens.</p>
        ) : (
          <>
            <p className="muted">
              Claims made by two or more notes that match one of your evergreens, and the notes that back an
              evergreen without linking to it.
            </p>
            <Agreement
              view={view}
              vault={vault}
              selection={selection}
              linkAction={(evergreen, notes) => (
                <LinkNotes evergreen={evergreen} notes={notes} onChange={() => setVersion((v) => v + 1)} />
              )}
            />
          </>
        )}
      </Step>

      {states.sides === "done" || states.agree === "done" ? (
        <section className="step" aria-label="Draft new evergreen">
          <h3 className="step-title">Draft new evergreen</h3>
          <DraftEvergreen topic={topic} claimIds={live} onChange={afterDraft} />
        </section>
      ) : null}
    </section>
  );
}

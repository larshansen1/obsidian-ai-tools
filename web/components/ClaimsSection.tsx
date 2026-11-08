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
import { Agreement, ClaimsTable, SplitSummaryView, SuggestedQuestions, type ClaimSelection } from "./ClaimsView";
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

const count = (n: number) => n.toLocaleString("en-US");
const plural = (n: number, word: string) => `${count(n)} ${word}${n === 1 ? "" : "s"}`;
const notesIn = (claims: ClaimsView["supporting"]) => new Set(claims.map((c) => c.path)).size;

/** "N claims for (from X notes), M against (from Y notes)" for the answer card. */
export function splitCounts(view: ClaimsView): string {
  const side = (claims: ClaimsView["supporting"]) => `from ${plural(notesIn(claims), "note")}`;
  return (
    `${plural(view.supporting.length, "claim")} for (${side(view.supporting)}), ` +
    `${count(view.pushing_back.length)} against (${side(view.pushing_back)})`
  );
}

/** The page leads with the answer once a question is chosen and the notes are read (#171). */
export function answerFirst(view: ClaimsView): boolean {
  return view.question !== null && view.next_step !== "read";
}

/** "1:05" for a run that has been working for 65 seconds. */
export function elapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

// `onWrite` hears about a saved or undone evergreen, so the page can re-read its evergreens.
// `onAnswerFirst` hears whether the section leads with the answer, so the page can move it up.
export function ClaimsSection({
  topic,
  onWrite,
  onAnswerFirst,
}: {
  topic: string;
  onWrite?: () => void;
  onAnswerFirst?: (answerFirst: boolean) => void;
}) {
  const [view, setView] = useState<ClaimsView | null>(null);
  const [vault, setVault] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [custom, setCustom] = useState("");
  // Bumped after a link write so the unlinked notes are read again.
  const [version, setVersion] = useState(0);
  // Claims ticked for "Draft new evergreen", in the order they were ticked.
  const [picked, setPicked] = useState<string[]>([]);
  // The question picker under "Change question" on the answer card.
  const [switching, setSwitching] = useState(false);
  // When the current run started, for the "Working… 1:05" line on the answer card.
  const [started, setStarted] = useState<number | null>(null);
  const [now, setNow] = useState(0);

  useEffect(() => {
    if (started === null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [started]);
  const leading = view !== null && answerFirst(view);

  useEffect(() => {
    onAnswerFirst?.(leading);
  }, [leading, onAnswerFirst]);

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
    const start = Date.now();
    setStarted(start);
    setNow(start);
    setNotice(null);
    try {
      const result = await runClaims(topic, approved);
      setView(result.view);
      setNotice(noticeFor(result));
    } catch (e) {
      setNotice({ kind: "error", message: (e as Error).message });
    } finally {
      setStarted(null);
      setBusy(false);
    }
  }

  async function choose(question: string) {
    const text = question.trim();
    if (!text) return;
    logUsage("action", "claims_question", topic);
    let saved: ClaimsView;
    try {
      saved = await saveQuestion(topic, text);
    } catch (e) {
      setNotice({ kind: "error", message: (e as Error).message });
      return;
    }
    setView(saved);
    setSwitching(false);
    setCustom("");
    // One click: a question that is not sorted and summarised yet is done right away (#169).
    // The run does every step still open (#173 follow-up): after notes changed that is the
    // questions and agreement too, not only the sort.
    if (saved.next_step === "sort" || saved.next_step === "analyse") await run(false);
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

  const agreeStep = (
    <Step number={4} title="Where they agree" state={states.agree}>
      {states.agree === "waiting" ? (
        <p className="hint">
          {leading
            ? "Runs with Sort and summarise. It finds claims several notes make that match your evergreens."
            : "Needs step 2 first. It finds claims several notes make that match your evergreens."}
        </p>
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
  );
  const draftStep = states.sides === "done" || states.agree === "done" ? (
      <section className="step" aria-label="Draft new evergreen">
        <h3 className="step-title">Draft new evergreen</h3>
        <DraftEvergreen topic={topic} claimIds={live} onChange={afterDraft} />
      </section>
    ) : null;

  // The suggested questions and the own-question box: step 2, or "Change question" on the answer.
  const picker = (
    <>
    {proposals.length > 0 ? (
      <SuggestedQuestions
        questions={proposals}
        chosen={view.question}
        vault={vault}
        busy={busy}
        onChoose={(q) => void choose(q)}
      />
    ) : view.next_step === "analyse" ? (
      <p className="hint">No suggestions yet.</p>
    ) : (
      <p className="hint">No question stood out in these notes. You can still write your own.</p>
    )}
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
    </>
  );

  if (leading) {
    const question = view.question as string;
    return (
      <section className="card" aria-label="Claims across sources" data-testid="claims">
        <div aria-label="Answer" role="region" data-testid="answer">
          <p className="muted answer-kicker">Your question</p>
          <h2 className="answer-question">{question}</h2>
          <p className="muted" data-testid="answer-counts">
            {view.summary
              ? splitCounts(view)
              : view.next_step === "analyse"
                ? "Your notes changed since the last analysis."
                : `Not sorted yet: ${plural(view.claim_count, "claim")} to place.`}{" "}
            <button type="button" aria-expanded={switching} disabled={busy} onClick={() => setSwitching((v) => !v)}>
              Change question
            </button>
          </p>
          {switching ? (
            <div className="answer-switcher" aria-label="Change question" role="region">
              {picker}
            </div>
          ) : null}
          {view.summary ? (
            <>
              <SplitSummaryView summary={view.summary} vault={vault} />
              <details>
                <summary>All claims for and against ({view.supporting.length + view.pushing_back.length})</summary>
                <ClaimsTable view={view} vault={vault} selection={selection} />
              </details>
            </>
          ) : null}
          {view.next_step === "sort" || view.next_step === "analyse" ? (
            <div className="step-action" data-testid="answer-work">
              <button type="button" className="primary" disabled={busy} onClick={() => void run(false)}>
                {busy ? "Working…" : "Sort and summarise"}
              </button>
              {busy && started !== null ? (
                <span className="muted" role="status">
                  {" "}
                  Working on your question: {elapsed(now - started)}. A big topic can take a few minutes.
                </span>
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
            </div>
          ) : null}
          {view.next_step === "done" && notice ? (
            <div role={notice.kind === "error" ? "alert" : "status"} className="warn">
              {notice.message}
            </div>
          ) : null}
        </div>

        <ul className="steps-done" aria-label="Finished steps">
          <li>✓ {plural(view.read_notes, "note")} read · {plural(view.claim_count, "claim")}</li>
          <li>✓ Question chosen</li>
          {view.summary ? <li>✓ Sorted and summarised</li> : null}
        </ul>

        {agreeStep}
        {draftStep}
      </section>
    );
  }

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
          <p className="hint">Read your notes first. Then the AI suggests questions that come from your notes.</p>
        ) : (
          <>
            <p className="step-status">
              {view.question ? "Question chosen. You can switch to another at any time." : "Pick the yes or no question to sort your claims by."}
            </p>
            {picker}
            {action("analyse")}
          </>
        )}
      </Step>

      <Step number={3} title="How the notes split" state={states.sides}>
        {view.question === null ? (
          <p className="hint">Choose a question in step 2. Your notes are then sorted and summarised.</p>
        ) : view.next_step === "sort" ? (
          <p className="step-status">
            The question is chosen: “{view.question}” Sort the {view.claim_count} claims and summarise the split.
          </p>
        ) : states.sides === "waiting" ? (
          <p className="hint">Finish the steps above first.</p>
        ) : (
          <>
            {view.summary ? <SplitSummaryView summary={view.summary} vault={vault} /> : null}
            <details>
              <summary>
                All claims for and against ({view.supporting.length + view.pushing_back.length})
              </summary>
              <ClaimsTable view={view} vault={vault} selection={selection} />
            </details>
          </>
        )}
        {action("sort")}
      </Step>

      {agreeStep}
      {draftStep}
    </section>
  );
}

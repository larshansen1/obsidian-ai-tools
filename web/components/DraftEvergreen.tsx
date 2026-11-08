"use client";

import { useState } from "react";
import { usePlanned } from "./WriteActions";
import { planEvergreen, startDraft, writeDraft } from "../lib/ingest";

// T8: start an evergreen from the picked claims. The AI writes a first draft, then each chat
// instruction revises it in place; the text stays editable by hand too. The draft lives only in
// this form until the saved file is previewed and approved; Discard or Cancel leaves the vault as
// it was.
type Draft = { title: string; body: string };
type Turn = { said: string; reply: string };
type Ask = { instruction: string | null; base: Draft };

export const FIRST_DRAFT = "Write a first draft";
export const UPDATED = "Done. The draft is updated.";

export const FROM_ANSWER = "Draft evergreen from this answer";

// `fromAnswer`: the draft answers the topic's chosen question, from its summary and `claimIds`
// (both sides, picked by the caller), with one click and no ticking.
export function DraftEvergreen({
  topic,
  claimIds,
  onChange,
  fromAnswer = false,
}: {
  topic: string;
  claimIds: string[];
  onChange: () => void;
  fromAnswer?: boolean;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  // The claims the draft started from: ticking more later does not change this draft.
  const [sources, setSources] = useState<string[]>([]);
  // Earlier versions, newest last, for "Undo last change".
  const [history, setHistory] = useState<Draft[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [instruction, setInstruction] = useState("");
  const [starting, setStarting] = useState(false);
  const [writing, setWriting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; retry: Ask | null } | null>(null);
  const { plan, card, error, open } = usePlanned(onChange);

  async function ask({ instruction: said, base }: Ask, ids: string[], approved: boolean) {
    setWriting(true);
    setNotice(null);
    try {
      const result = await writeDraft(topic, {
        claim_ids: ids,
        title: base.title,
        body: base.body,
        instruction: said,
        answer: fromAnswer,
        approved,
      });
      if (result.status === "done") {
        setHistory((h) => [...h, base]);
        setDraft({ title: result.title, body: result.body });
        setTurns((t) => [...t, { said: said ?? FIRST_DRAFT, reply: UPDATED }]);
        if (said !== null) setInstruction("");
        return;
      }
      const message = result.message ?? "The AI did not answer.";
      if (result.status === "needs_approval") {
        const cost = `Estimated cost: $${(result.estimate_usd ?? 0).toFixed(4)}.`;
        setNotice({ message: `${message} ${cost}`, retry: { instruction: said, base } });
      } else {
        setNotice({ message, retry: null });
      }
    } catch (e) {
      setNotice({ message: (e as Error).message, retry: null });
    } finally {
      setWriting(false);
    }
  }

  async function start() {
    setStarting(true);
    setStartError(null);
    const ids = [...claimIds];
    let first: Draft;
    try {
      first = { title: "", body: (await startDraft(topic, ids)).body };
    } catch (e) {
      setStartError((e as Error).message);
      return;
    } finally {
      setStarting(false);
    }
    setSources(ids);
    setHistory([]);
    setTurns([]);
    setInstruction("");
    setNotice(null);
    setDraft(first);
    await ask({ instruction: null, base: first }, ids, false);
  }

  function send() {
    const said = instruction.trim();
    if (draft === null || said === "" || writing) return;
    void ask({ instruction: said, base: draft }, sources, false);
  }

  function undoChange() {
    const previous = history[history.length - 1];
    if (previous === undefined) return;
    setHistory(history.slice(0, -1));
    setDraft(previous);
    setTurns((t) => [...t, { said: "Undo last change", reply: "Back to the version before." }]);
  }

  if (draft === null && fromAnswer) {
    return (
      <div className="write-action" data-testid="draft-from-answer">
        <button type="button" className="primary" disabled={claimIds.length === 0 || starting} onClick={() => void start()}>
          {FROM_ANSWER}
        </button>
        <span className="muted"> The AI writes a first draft. You then chat to improve it and save.</span>
        {startError ? <p role="alert">{startError}</p> : null}
      </div>
    );
  }
  if (draft === null) {
    return (
      <div className="write-action" data-testid="draft-evergreen">
        <button type="button" disabled={claimIds.length === 0 || starting} onClick={() => void start()}>
          Draft new evergreen{claimIds.length > 0 ? ` from ${claimIds.length} claim${claimIds.length === 1 ? "" : "s"}` : ""}
        </button>
        {claimIds.length === 0 ? <span className="muted"> Tick claims above to start a draft.</span> : null}
        {startError ? <p role="alert">{startError}</p> : null}
      </div>
    );
  }
  const retry = notice?.retry ?? null;
  return (
    <div className="edit-topic" data-testid="draft-evergreen">
      <p className="muted">Nothing is saved until you approve the new note.</p>
      <label className="draft-field">
        Title
        <input
          value={draft.title}
          readOnly={writing}
          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          placeholder="One idea, stated as a claim"
        />
      </label>
      <label className="draft-field">
        Text
        <textarea
          rows={18}
          value={draft.body}
          readOnly={writing}
          onChange={(e) => setDraft({ ...draft, body: e.target.value })}
        />
      </label>
      {turns.length > 0 ? (
        <ol className="draft-turns" aria-label="Changes so far">
          {turns.map((turn, i) => (
            <li key={i}>
              <strong>{turn.said}</strong> <span className="muted">{turn.reply}</span>
            </li>
          ))}
        </ol>
      ) : null}
      {writing ? (
        <p role="status" className="muted">
          The AI is working on the draft…
        </p>
      ) : null}
      {notice ? (
        <div role="alert" className="warn">
          {notice.message}{" "}
          {retry ? (
            <button type="button" onClick={() => void ask(retry, sources, true)}>
              Go ahead
            </button>
          ) : null}
        </div>
      ) : null}
      <label className="draft-field">
        Tell the AI what to change
        <textarea
          rows={2}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Make it shorter. Lead with the second point."
        />
      </label>
      <div className="chat-actions">
        <button type="button" disabled={writing || instruction.trim() === ""} onClick={send}>
          Send
        </button>
        <button type="button" disabled={writing || history.length === 0} onClick={undoChange}>
          Undo last change
        </button>
        <button
          type="button"
          disabled={open || writing || draft.title.trim() === ""}
          onClick={() => plan(() => planEvergreen(topic, draft.title, draft.body))}
        >
          Save
        </button>
        <button type="button" onClick={() => setDraft(null)}>
          Discard
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {card}
    </div>
  );
}

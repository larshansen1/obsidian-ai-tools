import type { ReactNode } from "react";
import { noteLabel, noteUrl } from "../lib/chat";
import type { ClaimItem, ClaimsView, QuestionKind, SharedClaim, SplitSummary, SuggestedQuestion } from "../lib/claims";

function SourceLink({ vault, path, title }: { vault: string; path: string; title: string }) {
  return (
    <a className="chat-cite" href={noteUrl(vault, path)} title={path}>
      {title || noteLabel(path)}
    </a>
  );
}

// Picked claims for "Draft new evergreen" (T8). `toggle` adds the ids, or removes them if all are picked.
export type ClaimSelection = { selected: ReadonlySet<string>; toggle: (ids: string[]) => void };

function Pick({ ids, label, selection }: { ids: string[]; label: string; selection?: ClaimSelection }) {
  if (!selection) return null;
  return (
    <>
      <input
        type="checkbox"
        aria-label={label}
        checked={ids.every((id) => selection.selected.has(id))}
        onChange={() => selection.toggle(ids)}
      />{" "}
    </>
  );
}

function ClaimList({
  label,
  caption,
  claims,
  vault,
  selection,
}: {
  label: string;
  caption: string;
  claims: ClaimItem[];
  vault: string;
  selection?: ClaimSelection;
}) {
  return (
    <div className="claims-side">
      <h4>
        {label} ({claims.length})
      </h4>
      <p className="muted claims-caption">{caption}</p>
      {claims.length === 0 ? (
        <p className="hint">None.</p>
      ) : (
        <ul className="list" aria-label={label}>
          {claims.map((c) => (
            <li key={c.id}>
              <Pick ids={[c.id]} label={`Pick claim: ${c.text}`} selection={selection} />
              {c.text} <SourceLink vault={vault} path={c.path} title={c.title} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The question with its claims on two sides (T3). Used by the topic page and the chat card (C5).
export function ClaimsTable({
  view,
  vault,
  selection,
}: {
  view: ClaimsView;
  vault: string;
  selection?: ClaimSelection;
}) {
  if (view.question === null) {
    return <p className="hint">Pick a question to sort the {view.claim_count} claims into two sides.</p>;
  }
  return (
    <div data-testid="claims-table">
      <p className="claims-question">{view.question}</p>
      <div className="claims-sides">
        <ClaimList
          label="Supporting"
          caption="Points to yes"
          claims={view.supporting}
          vault={vault}
          selection={selection}
        />
        <ClaimList
          label="Pushing back"
          caption="Points to no"
          claims={view.pushing_back}
          vault={vault}
          selection={selection}
        />
      </div>
      {view.unrelated > 0 ? <p className="muted">{view.unrelated} claims do not bear on this question.</p> : null}
    </div>
  );
}

// Renders an action for a shared claim's unlinked notes (the topic page's Link button).
export type LinkAction = (evergreen: string, notes: string[]) => ReactNode;

// Closest notes shown up front; the rest open on click (#173). Claims come closest first.
const CLOSEST_NOTES = 3;

function Shared({
  item,
  vault,
  linkAction,
  selection,
}: {
  item: SharedClaim;
  vault: string;
  linkAction?: LinkAction;
  selection?: ClaimSelection;
}) {
  const notes = [...new Map(item.claims.map((c) => [c.path, c.title])).entries()].map(([path, title]) => ({
    path,
    title,
  }));
  const unlinked = new Set(item.unlinked_notes.map((n) => n.path));
  const links = (list: { path: string; title: string }[]) =>
    list.map((n, i) => (
      <span key={n.path}>
        {i > 0 ? ", " : ""}
        <SourceLink vault={vault} path={n.path} title={n.title} />
      </span>
    ));
  return (
    <li>
      <Pick ids={item.claims.map((c) => c.id)} label={`Pick claim: ${item.text}`} selection={selection} />
      <strong>{item.text}</strong>
      <div className="muted">
        In {item.claims.length} claims from {notes.length} notes
        {item.evergreen ? (
          <>
            {" "}
            · matches evergreen <SourceLink vault={vault} path={item.evergreen.path} title={item.evergreen.title} />
          </>
        ) : (
          " · no evergreen matches yet"
        )}
      </div>
      <div data-testid="closest-notes">Closest: {links(notes.slice(0, CLOSEST_NOTES))}</div>
      <details>
        <summary>
          Show all {notes.length} notes
          {unlinked.size > 0 ? ` (${unlinked.size} not linked to the evergreen)` : ""}
        </summary>
        <ul className="list" aria-label={`Notes behind: ${item.text}`}>
          {notes.map((n) => (
            <li key={n.path}>
              <SourceLink vault={vault} path={n.path} title={n.title} />
              {unlinked.has(n.path) ? <span className="muted"> · not linked</span> : null}
            </li>
          ))}
        </ul>
        {linkAction && item.evergreen && unlinked.size > 0
          ? linkAction(
              item.evergreen.path,
              item.unlinked_notes.map((n) => n.path),
            )
          : null}
      </details>
    </li>
  );
}

// Shared claims against my evergreens, with the supporting notes that lack a link (T4).
export function Agreement({
  view,
  vault,
  linkAction,
  selection,
}: {
  view: ClaimsView;
  vault: string;
  linkAction?: LinkAction;
  selection?: ClaimSelection;
}) {
  if (view.shared.length === 0) return <p className="hint">No claim is shared by two or more notes yet.</p>;
  return (
    <ul className="list" data-testid="agreement">
      {view.shared.map((item) => (
        <Shared key={item.text} item={item} vault={vault} linkAction={linkAction} selection={selection} />
      ))}
    </ul>
  );
}

const KIND_LABEL: Record<QuestionKind, string> = {
  tension: "Notes disagree",
  recurring: "Keeps coming back",
  carry_over: "Does it carry over?",
};

// Suggested questions with the notes each is anchored in (#166). Only "Notes disagree" questions
// are yes or no, so only they can be used to sort claims; the others are for reading the notes.
export function SuggestedQuestions({
  questions,
  chosen,
  vault,
  busy,
  onChoose,
}: {
  questions: SuggestedQuestion[];
  chosen: string | null;
  vault: string;
  busy: boolean;
  onChoose: (question: string) => void;
}) {
  return (
    <ul className="list" data-testid="questions">
      {questions.map((q) => (
        <li key={q.text}>
          <span className="muted">{KIND_LABEL[q.kind]}:</span> {q.text}{" "}
          {q.kind === "tension" ? (
            <button type="button" disabled={busy} onClick={() => onChoose(q.text)}>
              {q.text === chosen ? "Chosen" : "Use this question"}
            </button>
          ) : null}
          {q.why ? <p className="muted claims-caption">{q.why}</p> : null}
          <details>
            <summary>From {q.anchors.length === 1 ? "1 claim" : `${q.anchors.length} claims`}</summary>
            <ul className="list" aria-label={`Claims behind: ${q.text}`}>
              {q.anchors.map((c) => (
                <li key={c.id}>
                  {c.text} <SourceLink vault={vault} path={c.path} title={c.title} />
                </li>
              ))}
            </ul>
          </details>
        </li>
      ))}
    </ul>
  );
}

function Points({ label, points }: { label: string; points: string[] }) {
  if (points.length === 0) return null;
  return (
    <>
      <h4>{label}</h4>
      <ul className="list" aria-label={label}>
        {points.map((point) => (
          <li key={point}>{point}</li>
        ))}
      </ul>
    </>
  );
}

// The summary of how the notes split on the question (#169), shown above the claim lists.
export function SplitSummaryView({ summary, vault }: { summary: SplitSummary; vault: string }) {
  return (
    <div data-testid="split-summary">
      <p>
        <strong>{summary.bottom_line}</strong>
      </p>
      <Points label="Where they agree" points={summary.agree} />
      <Points label="Where they split" points={summary.split} />
      <Points label="What is missing" points={summary.missing} />
      {summary.read.length > 0 ? (
        <>
          <h4>Worth reading</h4>
          <ul className="list" aria-label="Worth reading">
            {summary.read.map((note) => (
              <li key={note.path}>
                <SourceLink vault={vault} path={note.path} title={note.title} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

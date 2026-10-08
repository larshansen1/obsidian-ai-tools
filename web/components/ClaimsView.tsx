import type { ReactNode } from "react";
import { noteLabel, noteUrl } from "../lib/chat";
import type { ClaimItem, ClaimsView, SharedClaim } from "../lib/claims";

function SourceLink({ vault, path, title }: { vault: string; path: string; title: string }) {
  return (
    <a className="chat-cite" href={noteUrl(vault, path)} title={path}>
      {title || noteLabel(path)}
    </a>
  );
}

function ClaimList({
  label,
  caption,
  claims,
  vault,
}: {
  label: string;
  caption: string;
  claims: ClaimItem[];
  vault: string;
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
              {c.text} <SourceLink vault={vault} path={c.path} title={c.title} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The question with its claims on two sides (T3). Used by the topic page and the chat card (C5).
export function ClaimsTable({ view, vault }: { view: ClaimsView; vault: string }) {
  if (view.question === null) {
    return <p className="hint">Pick a question to sort the {view.claim_count} claims into two sides.</p>;
  }
  return (
    <div data-testid="claims-table">
      <p className="claims-question">{view.question}</p>
      <div className="claims-sides">
        <ClaimList label="Supporting" caption="Points to yes" claims={view.supporting} vault={vault} />
        <ClaimList label="Pushing back" caption="Points to no" claims={view.pushing_back} vault={vault} />
      </div>
      {view.unrelated > 0 ? <p className="muted">{view.unrelated} claims do not bear on this question.</p> : null}
    </div>
  );
}

// Renders an action for a shared claim's unlinked notes (the topic page's Link button).
export type LinkAction = (evergreen: string, notes: string[]) => ReactNode;

function Shared({ item, vault, linkAction }: { item: SharedClaim; vault: string; linkAction?: LinkAction }) {
  return (
    <li>
      <strong>{item.text}</strong>
      <div className="muted">
        In {item.claims.length} claims from {new Set(item.claims.map((c) => c.path)).size} notes
        {item.evergreen ? (
          <>
            {" "}
            · matches evergreen <SourceLink vault={vault} path={item.evergreen.path} title={item.evergreen.title} />
          </>
        ) : (
          " · no evergreen matches yet"
        )}
      </div>
      {item.unlinked_notes.length > 0 ? (
        <div>
          Support it but do not link to it:{" "}
          {item.unlinked_notes.map((n, i) => (
            <span key={n.path}>
              {i > 0 ? ", " : ""}
              <SourceLink vault={vault} path={n.path} title={n.title} />
            </span>
          ))}
          {linkAction && item.evergreen
            ? linkAction(
                item.evergreen.path,
                item.unlinked_notes.map((n) => n.path),
              )
            : null}
        </div>
      ) : null}
    </li>
  );
}

// Shared claims against my evergreens, with the supporting notes that lack a link (T4).
export function Agreement({ view, vault, linkAction }: { view: ClaimsView; vault: string; linkAction?: LinkAction }) {
  if (view.shared.length === 0) return <p className="hint">No claim is shared by two or more notes yet.</p>;
  return (
    <ul className="list" data-testid="agreement">
      {view.shared.map((item) => (
        <Shared key={item.text} item={item} vault={vault} linkAction={linkAction} />
      ))}
    </ul>
  );
}

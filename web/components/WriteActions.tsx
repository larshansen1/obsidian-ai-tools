"use client";

import { useState } from "react";
import { WriteCard } from "./WriteCard";
import { planLinks, planTopicTags, splitTags, type PendingWrite, type WriteOutcome } from "../lib/writes";

// Plans a change on demand and shows its card. `onChange` runs after a write or an undo.
function usePlanned(onChange: () => void) {
  const [pending, setPending] = useState<PendingWrite | null>(null);
  const [result, setResult] = useState<WriteOutcome | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  async function plan(make: () => Promise<PendingWrite>) {
    setError(null);
    setResult(undefined);
    try {
      setPending(await make());
    } catch (e) {
      setPending(null);
      setError((e as Error).message);
    }
  }

  const card = pending ? (
    <WriteCard
      pending={pending}
      result={result}
      onResult={(o) => {
        setResult(o);
        if (o.status === "written") onChange();
      }}
      onUndo={onChange}
    />
  ) : null;
  return { plan, card, error, open: pending !== null && result === undefined };
}

// W1: link the notes that back an evergreen but do not link to it.
export function LinkNotes({
  evergreen,
  notes,
  onChange,
}: {
  evergreen: string;
  notes: string[];
  onChange: () => void;
}) {
  const { plan, card, error, open } = usePlanned(onChange);
  return (
    <div className="write-action">
      {open ? null : (
        <button type="button" onClick={() => plan(() => planLinks(evergreen, notes))}>
          Link {notes.length === 1 ? "it" : "them"} to the evergreen
        </button>
      )}
      {error ? <p role="alert">{error}</p> : null}
      {card}
    </div>
  );
}

// W2, T7: add or remove the topic's tags in .kai/topics.yaml.
export function EditTopic({ topic, tags, onChange }: { topic: string; tags: string[]; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [remove, setRemove] = useState<string[]>([]);
  const [add, setAdd] = useState("");
  const { plan, card, error, open } = usePlanned(() => {
    setRemove([]);
    setAdd("");
    onChange();
  });

  if (!editing) {
    return (
      <button type="button" onClick={() => setEditing(true)}>
        Edit topic
      </button>
    );
  }
  const toggle = (tag: string) =>
    setRemove((current) => (current.includes(tag) ? current.filter((t) => t !== tag) : [...current, tag]));
  return (
    <div className="edit-topic" data-testid="edit-topic">
      <p className="muted">Click a tag to remove it. Changes are saved to .kai/topics.yaml.</p>
      <div className="tag-chips" role="group" aria-label="Tags">
        {tags.map((tag) => (
          <button
            key={tag}
            type="button"
            className="tag-chip"
            aria-pressed={remove.includes(tag)}
            aria-label={remove.includes(tag) ? `Keep ${tag}` : `Remove ${tag}`}
            onClick={() => toggle(tag)}
          >
            {tag}
          </button>
        ))}
      </div>
      <label>
        Add tags{" "}
        <input value={add} onChange={(e) => setAdd(e.target.value)} placeholder="sleep, rest" />
      </label>
      <div className="chat-actions">
        <button type="button" disabled={open} onClick={() => plan(() => planTopicTags(topic, splitTags(add), remove))}>
          Preview change
        </button>
        <button type="button" onClick={() => setEditing(false)}>
          Close
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {card}
    </div>
  );
}

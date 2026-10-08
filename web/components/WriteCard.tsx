"use client";

import { useState } from "react";
import {
  applyWrite,
  cancelWrite,
  undoWrite,
  type FilePreview,
  type PendingWrite,
  type WriteOutcome,
} from "../lib/writes";

const MARK = { "+": "+", "-": "-", " ": " ", "@": "…" } as const;
const KIND = { "+": "add", "-": "del", " ": "same", "@": "gap" } as const;

function Diff({ file }: { file: FilePreview }) {
  return (
    <div className="write-file">
      <div className="write-file-name">{file.file}</div>
      <pre className="write-diff">
        {file.lines.map((line, i) => (
          <div key={i} className={`write-line write-line-${KIND[line.op]}`}>
            {MARK[line.op]} {line.text}
          </div>
        ))}
      </pre>
    </div>
  );
}

// The exact change, then Approve or Cancel. Nothing is written before the click (C8).
// After a write it offers Undo. `onResult` gets the answer to the card (a chat card hands
// it back to the model); `onUndo` hears about an undo so a screen can reload its data.
export function WriteCard({
  pending,
  result,
  onResult,
  onUndo,
}: {
  pending: PendingWrite;
  result?: WriteOutcome;
  onResult: (outcome: WriteOutcome) => void;
  onUndo?: (outcome: WriteOutcome) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [undone, setUndone] = useState<WriteOutcome | null>(null);

  async function act(step: () => Promise<WriteOutcome>, done: (o: WriteOutcome) => void) {
    setBusy(true);
    setError(null);
    try {
      done(await step());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const shown = undone ?? result;
  return (
    <div className="chat-tool write-card" data-testid="write-card">
      <strong>{pending.summary}</strong>
      {pending.files.map((f) => (
        <Diff key={f.file} file={f} />
      ))}
      {shown === undefined ? (
        <div className="chat-actions">
          <button disabled={busy} onClick={() => act(() => applyWrite(pending.id), onResult)}>
            Approve
          </button>
          <button disabled={busy} onClick={() => act(() => cancelWrite(pending.id), onResult)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="write-outcome">
          <span className={shown.status === "refused" ? "warn" : "muted"} role="status">
            {shown.status === "written" ? `Saved: ${shown.message}` : shown.message}
          </span>
          {shown.status === "written" ? (
            <button
              disabled={busy}
              onClick={() =>
                act(
                  () => undoWrite(shown.id),
                  (o) => {
                    if (o.status === "undone") {
                      setUndone(o);
                      onUndo?.(o);
                    } else setError(o.message);
                  },
                )
              }
            >
              Undo
            </button>
          ) : null}
        </div>
      )}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchWrites, formatWhen, undoWrite, type LoggedWrite } from "../lib/writes";

// The write log (W5): the last 20 writes, each with Undo while its files are unchanged.
export function ChangesScreen() {
  const [writes, setWrites] = useState<LoggedWrite[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Bumped after an undo so the list is read again.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchWrites()
      .then((next) => {
        if (!cancelled) setWrites(next);
      })
      .catch((e: Error) => {
        if (!cancelled) setMessage(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [version]);

  async function undo(id: string) {
    setBusy(true);
    try {
      setMessage((await undoWrite(id)).message);
      setVersion((v) => v + 1);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page">
      <p className="back">
        <Link href="/">Back to topic map</Link>
      </p>
      <h1 className="page-title">Recent changes</h1>
      {message ? <p role="status">{message}</p> : null}
      {writes === null ? (
        <p>Loading…</p>
      ) : writes.length === 0 ? (
        <p className="hint">No changes yet. Links and topic edits show up here.</p>
      ) : (
        <ul className="list card" data-testid="changes">
          {writes.map((w) => (
            <li key={w.id} className="change">
              <strong>{w.summary}</strong>{" "}
              <span className="muted">
                {formatWhen(w.written_at)} · {w.files.join(", ")}
              </span>{" "}
              {w.undone ? (
                <span className="muted">(undone)</span>
              ) : (
                <button type="button" disabled={busy} onClick={() => undo(w.id)}>
                  Undo
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

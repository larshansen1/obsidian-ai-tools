"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchIngests, monthCount, thisMonth, type IngestLog } from "../lib/ingest";
import { fetchWrites, formatWhen, undoWrite, type LoggedWrite } from "../lib/writes";

const STATUS = { queued: "Queued", done: "Done", failed: "Failed" } as const;

// Sources sent to kai (W3), with the "ingested from app suggestions per month" measure.
function SentToKai({ log }: { log: IngestLog }) {
  const count = monthCount(log, thisMonth());
  return (
    <section className="card" aria-label="Sent to kai">
      <h2>Sent to kai</h2>
      <p data-testid="ingest-count">
        New notes from suggestions this month: <strong>{count}</strong>
      </p>
      {log.per_month.length > 1 ? (
        <p className="muted">
          {log.per_month.map((m) => `${m.month}: ${m.count}`).join(" · ")}
        </p>
      ) : null}
      {log.jobs.length === 0 ? (
        <p className="hint">Nothing sent yet. Use Ingest on a suggested source in the chat.</p>
      ) : (
        <ul className="list" data-testid="ingests">
          {log.jobs.map((j) => (
            <li key={j.id}>
              <strong>{j.title}</strong>{" "}
              <span className={j.status === "failed" ? "warn" : "muted"}>
                {STATUS[j.status]}: {j.message} · {formatWhen(j.requested_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// The write log (W5): the last 20 writes, each with Undo while its files are unchanged.
export function ChangesScreen() {
  const [writes, setWrites] = useState<LoggedWrite[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Bumped after an undo so the list is read again.
  const [version, setVersion] = useState(0);
  const [ingests, setIngests] = useState<IngestLog | null>(null);

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

  useEffect(() => {
    let cancelled = false;
    fetchIngests()
      .then((next) => {
        if (!cancelled) setIngests(next);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

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
        <p className="hint">No changes yet. Links, topic edits and new evergreens show up here.</p>
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
      {ingests ? <SentToKai log={ingests} /> : null}
    </main>
  );
}

"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { fetchAiStatus, noteUrl } from "../lib/chat";
import { fetchIngests, monthCount, thisMonth, type IngestLog } from "../lib/ingest";
import { fetchWrites, formatWhen, undoWrite, type LoggedWrite } from "../lib/writes";

const STATUS = { queued: "Queued", done: "Done", failed: "Failed" } as const;

// A vault note opens in Obsidian. Settings files under .kai are not notes, and without the vault
// name there is nowhere to link to, so both stay plain text.
function isNote(path: string): boolean {
  return path.endsWith(".md") && !path.startsWith(".kai/");
}

function NoteLink({ path, text, vault }: { path: string; text: string; vault: string }) {
  return vault && isNote(path) ? <a href={noteUrl(vault, path)}>{text}</a> : <>{text}</>;
}

// Sources sent to kai (W3), with the "ingested from app suggestions per month" measure.
function SentToKai({ log, vault }: { log: IngestLog; vault: string }) {
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
              <strong>{j.note_path ? <NoteLink path={j.note_path} text={j.title} vault={vault} /> : j.title}</strong>{" "}
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
  // The write whose Undo was clicked. Undo changes files in the vault, so it asks first (#188).
  const [asking, setAsking] = useState<string | null>(null);
  // Bumped after an undo so the list is read again.
  const [version, setVersion] = useState(0);
  const [ingests, setIngests] = useState<IngestLog | null>(null);
  const [vault, setVault] = useState("");

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

  useEffect(() => {
    let cancelled = false;
    fetchAiStatus()
      .then((status) => {
        if (!cancelled) setVault(status.vault_name);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  async function undo(id: string) {
    setAsking(null);
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
                {formatWhen(w.written_at)} ·{" "}
                {w.files.map((file, i) => (
                  <Fragment key={file}>
                    {i > 0 ? ", " : ""}
                    <NoteLink path={file} text={file} vault={vault} />
                  </Fragment>
                ))}
              </span>{" "}
              {w.undone ? (
                <span className="muted">(undone)</span>
              ) : asking === w.id ? (
                <span role="group" aria-label="Undo this change">
                  Undo this change?{" "}
                  <button type="button" disabled={busy} onClick={() => undo(w.id)}>
                    Yes, undo
                  </button>{" "}
                  <button type="button" onClick={() => setAsking(null)}>
                    Cancel
                  </button>
                </span>
              ) : (
                <button type="button" disabled={busy} onClick={() => setAsking(w.id)}>
                  Undo
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {ingests ? <SentToKai log={ingests} vault={vault} /> : null}
    </main>
  );
}

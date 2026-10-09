"use client";

import { useEffect, useState } from "react";
import { noteUrl } from "../lib/chat";
import { fetchIngest, sendToKai, type IngestJob } from "../lib/ingest";

const LABEL = { queued: "Queued", done: "Done", failed: "Failed" } as const;

// Ingest on a source card (W3, C7): confirm, send to kai, then show queued, done or failed.
// kai can take minutes, so the job is polled every `pollMs` until it finishes.
export function IngestButton({
  url,
  title,
  topic,
  vault,
  pollMs = 2000,
}: {
  url: string;
  title: string;
  topic: string | null;
  vault: string;
  pollMs?: number;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<IngestJob | null>(null);

  const queuedId = job?.status === "queued" ? job.id : null;
  useEffect(() => {
    if (queuedId === null) return;
    const timer = setInterval(() => {
      fetchIngest(queuedId)
        .then((next) => {
          if (next.status !== "queued") setJob(next);
        })
        .catch(() => undefined); // a missed poll is retried on the next tick
    }, pollMs);
    return () => clearInterval(timer);
  }, [queuedId, pollMs]);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      setJob(await sendToKai(url, title, topic));
      setConfirming(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (job !== null) {
    return (
      <div className="ingest" data-testid="ingest">
        <span role="status" className={job.status === "failed" ? "warn" : "muted"}>
          {LABEL[job.status]}: {job.message}
        </span>{" "}
        {job.status === "done" && job.note_path ? (
          <a href={noteUrl(vault, job.note_path)}>Open note</a>
        ) : null}
        {job.status === "failed" ? (
          <button type="button" onClick={() => setJob(null)}>
            Try again
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="ingest" data-testid="ingest">
      {confirming ? (
        <div className="write-card">
          <p>
            Send this source to kai? kai fetches it and writes a new note to your inbox, using its own model. That
            costs a little money.
          </p>
          <div className="chat-actions">
            <button type="button" disabled={busy} onClick={() => void send()}>
              Send to kai
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" onClick={() => setConfirming(true)}>
          Ingest
        </button>
      )}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}

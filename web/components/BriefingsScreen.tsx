"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { IngestButton } from "./IngestButton";
import { fetchAiStatus, noteUrl } from "../lib/chat";
import { dismissBriefing, fetchBriefings, mentions, withoutLink, type BriefingList, type BriefingRow } from "../lib/briefings";
import type { IngestJob } from "../lib/ingest";
import { logUsage } from "../lib/topicMap";
import { formatWhen } from "../lib/writes";

type Added = { id: string; title: string; notePath: string | null; message: string };

// The reading list from the daily briefings (#197). Every title is shown as plain text: it comes
// from an agent that reads the open web, so it is never rendered as Markdown or HTML.
function Row({
  row,
  topic,
  vault,
  onDone,
  onDismissed,
}: {
  row: BriefingRow;
  topic: string | null;
  vault: string;
  onDone: (row: BriefingRow, job: IngestJob) => void;
  onDismissed: (row: BriefingRow) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function dismiss() {
    setBusy(true);
    setError(null);
    try {
      await dismissBriefing(row.id);
      logUsage("action", "briefing_dismiss", row.url);
      onDismissed(row);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <li className="candidate" data-testid="briefing">
      <div className="candidate-head">
        <strong>{row.title}</strong>
      </div>
      <div className="muted">
        {row.site} · {row.date}
        {row.section ? ` · ${row.section}` : ""} · {mentions(row.times_seen)}
      </div>
      <a
        className="candidate-preview"
        href={row.url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => logUsage("action", "briefing_preview", row.url)}
      >
        Preview
      </a>
      <IngestButton url={row.url} title={row.title} topic={topic} vault={vault} onDone={(job) => onDone(row, job)} />
      <button type="button" disabled={busy} onClick={() => void dismiss()}>
        Dismiss
      </button>
      {error ? <p role="alert">{error}</p> : null}
    </li>
  );
}

export function BriefingsScreen() {
  const [list, setList] = useState<BriefingList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [vault, setVault] = useState("");
  const [added, setAdded] = useState<Added[]>([]);

  useEffect(() => {
    logUsage("screen_view", "briefings");
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchBriefings()
      .then((next) => {
        if (!cancelled) setList(next);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    // Without the vault name the "Open note" link has no target, so it is left out.
    fetchAiStatus()
      .then((status) => {
        if (!cancelled) setVault(status.vault_name);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  function ingested(row: BriefingRow, job: IngestJob) {
    logUsage("action", "briefing_ingest", row.url);
    setList((current) => (current === null ? current : withoutLink(current, row.id)));
    setAdded((current) => [...current, { id: row.id, title: row.title, notePath: job.note_path, message: job.message }]);
  }

  function dismissed(row: BriefingRow) {
    setList((current) => (current === null ? current : withoutLink(current, row.id)));
  }

  return (
    <main className="page">
      <p className="back">
        <Link href="/">Back to topic map</Link>
      </p>
      <h1 className="page-title">Reading list</h1>
      {error ? <p role="alert">{error}</p> : null}
      {list === null && !error ? <p>Loading…</p> : null}
      {added.length > 0 ? (
        <ul className="list card" data-testid="added" aria-label="Added to the vault">
          {added.map((a) => (
            <li key={a.id}>
              <strong>{a.title}</strong> <span className="muted">{a.message}</span>{" "}
              {a.notePath && vault ? <a href={noteUrl(vault, a.notePath)}>Open note</a> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {list !== null ? (
        <>
          <p className="muted">
            {list.last_pull ? `Last pull: ${formatWhen(list.last_pull)}` : "Not pulled yet."}
            {list.pending_checks > 0
              ? ` · ${list.pending_checks} new links are still being checked and show up after the next pull.`
              : ""}
          </p>
          {list.groups.length === 0 ? (
            <p className="hint">
              Nothing to read. Run <code>compass briefings pull</code> to fetch your briefings.
            </p>
          ) : (
            list.groups.map((group) => (
              <section className="card" key={group.topic_id ?? "none"} aria-label={group.topic_name ?? "No topic"}>
                <h2>{group.topic_name ?? "No topic"}</h2>
                <ul className="candidates">
                  {group.links.map((row) => (
                    <Row
                      key={row.id}
                      row={row}
                      topic={group.topic_id}
                      vault={vault}
                      onDone={ingested}
                      onDismissed={dismissed}
                    />
                  ))}
                </ul>
              </section>
            ))
          )}
        </>
      ) : null}
    </main>
  );
}

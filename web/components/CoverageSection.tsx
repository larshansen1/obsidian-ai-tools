"use client";

import { useEffect, useState } from "react";
import { fetchCoverage, type CoverageGaps } from "../lib/coverage";
import { CoverageBars } from "./CoverageBars";

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

export function CoverageSection({ topic }: { topic: string }) {
  const [gaps, setGaps] = useState<CoverageGaps | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchCoverage(topic)
      .then((g) => live && setGaps(g))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [topic]);

  return (
    <section className="card" aria-label="Coverage gaps" data-testid="coverage">
      <h2>Coverage gaps</h2>
      {error && <p className="warn">{error}</p>}
      {!error && !gaps && <p className="hint">Loading…</p>}
      {gaps && gaps.count === 0 && (
        <p className="hint">
          Your notes cite no sources that are missing from the vault.
        </p>
      )}
      {gaps && gaps.count > 0 && (
        <>
          <p className="hint">
            {plural(gaps.count, "source")} cited in {plural(gaps.notes, "note")}{" "}
            but not in the vault. Ask the chat what to read next.
          </p>
          <CoverageBars byType={gaps.by_type} />
        </>
      )}
    </section>
  );
}

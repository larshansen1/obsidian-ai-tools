export type TypeCount = { type: string; count: number };

export type CoverageGaps = {
  topic: string;
  count: number;
  notes: number;
  by_type: TypeCount[];
  notes_for_model?: string[];
};

export type SourceCandidate = {
  url: string;
  title: string;
  type: string;
  origin: "citation" | "web";
  cited_in: string | null;
  why: string;
};

export type SourceCandidates = {
  candidates: SourceCandidate[];
  not_found: number;
  notes_for_model?: string[];
};

export async function fetchCoverage(topic: string): Promise<CoverageGaps> {
  const response = await fetch(
    `/api/topics/${encodeURIComponent(topic)}/coverage`,
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.json();
}

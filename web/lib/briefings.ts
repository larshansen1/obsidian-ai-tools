// The reading list built from daily briefings (#197).

export type BriefingRow = {
  id: string;
  url: string;
  // Plain text from an untrusted briefing: render it as text, never as Markdown or HTML.
  title: string;
  site: string;
  date: string;
  section: string | null;
  times_seen: number;
};

export type BriefingGroup = {
  // null is the "no topic" group.
  topic_id: string | null;
  topic_name: string | null;
  links: BriefingRow[];
};

export type BriefingList = {
  groups: BriefingGroup[];
  pending_checks: number;
  last_pull: string | null;
};

export async function fetchBriefings(): Promise<BriefingList> {
  const response = await fetch("/api/briefings");
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : `Request failed (${response.status})`);
  }
  return (await response.json()) as BriefingList;
}

export async function dismissBriefing(id: string): Promise<void> {
  const response = await fetch(`/api/briefings/${encodeURIComponent(id)}/dismiss`, { method: "POST" });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : `Request failed (${response.status})`);
  }
}

export function mentions(count: number): string {
  return count === 1 ? "1 briefing" : `${count} briefings`;
}

// The list without one link; groups left empty go with it.
export function withoutLink(list: BriefingList, id: string): BriefingList {
  const groups = list.groups
    .map((g) => ({ ...g, links: g.links.filter((l) => l.id !== id) }))
    .filter((g) => g.links.length > 0);
  return { ...list, groups };
}

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { logUsage } from "../lib/topicMap";
import { fetchTopics, topicFromPath, topicHref, topicOptionLabel, type TopicSummary } from "../lib/topicPage";

const LINKS = [
  { href: "/", label: "Topic map" },
  { href: "/briefings", label: "Reading list" },
  { href: "/changes", label: "Recent changes" },
];

/** The menu on every screen (#186): the map, the changes list, and any topic page in one click. */
export function SiteHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const [topics, setTopics] = useState<TopicSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    // Without the list the links still work, so a failed load only leaves the picker out.
    fetchTopics()
      .then((next) => {
        if (!cancelled) setTopics(next);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const open = topicFromPath(pathname);
  return (
    <header className="site-header">
      <nav className="site-nav" aria-label="Main">
        <span className="site-name">Vault Compass</span>
        {LINKS.map((link) => (
          <Link key={link.href} href={link.href} aria-current={pathname === link.href ? "page" : undefined}>
            {link.label}
          </Link>
        ))}
        {topics.length > 0 ? (
          <select
            aria-label="Go to topic"
            value={topics.some((t) => t.id === open) ? (open as string) : ""}
            onChange={(event) => {
              const id = event.target.value;
              if (!id) return;
              logUsage("action", "pick_topic", id);
              router.push(topicHref(id, "30"));
            }}
          >
            <option value="">Go to topic…</option>
            {topics.map((t) => (
              <option key={t.id} value={t.id}>
                {topicOptionLabel(t)}
              </option>
            ))}
          </select>
        ) : null}
      </nav>
    </header>
  );
}

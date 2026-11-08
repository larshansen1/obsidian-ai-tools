// Toolbar badge that shows whether the page in a tab is already in the vault.
// Kept free of chrome.* calls so node:test can drive it with fakes; the
// service worker (background.js) passes in the real lookup and badge setter.

export const BADGE_NONE = Object.freeze({ text: "", color: null });
export const BADGE_EXISTS = Object.freeze({ text: "✓", color: "#2e7d32" });
export const BADGE_IN_PROGRESS = Object.freeze({ text: "…", color: "#b26a00" });

// Answers stay fresh this long. Notes rarely change under an open tab, and an
// ingest from this extension clears the entry right away.
export const CACHE_TTL_MS = 60_000;
// After the server fails to answer, skip lookups this long so a stopped
// server is not hit on every tab switch.
export const OFFLINE_BACKOFF_MS = 30_000;
// While an ingest runs, ask again this often until it finishes.
export const IN_PROGRESS_RECHECK_MS = 5_000;

export function isLookupUrl(url) {
  return typeof url === "string" && /^https?:\/\//i.test(url);
}

export function badgeFor(lookupResult) {
  if (lookupResult?.in_progress === true) return BADGE_IN_PROGRESS;
  if (lookupResult?.exists === true) return BADGE_EXISTS;
  return BADGE_NONE;
}

/**
 * @param {object} deps
 * @param {(url: string) => Promise<object|null>} deps.lookup  resolves to the
 *   /lookup JSON, null for a non-OK reply; rejects when the server is down.
 * @param {(tabId: number, badge: {text: string, color: string|null}) => void} deps.setBadge
 * @param {() => number} deps.now
 * @param {(fn: () => void, ms: number) => void} deps.schedule
 */
export function createBadgeUpdater({ lookup, setBadge, now, schedule }) {
  const cache = new Map(); // url -> { badge, at }
  const pending = new Map(); // url -> Promise<badge>
  const latestUrl = new Map(); // tabId -> url the tab shows now
  let offlineUntil = 0;

  async function resolve(url) {
    const cached = cache.get(url);
    if (cached && now() - cached.at < CACHE_TTL_MS) return cached.badge;
    if (now() < offlineUntil) return BADGE_NONE;
    if (pending.has(url)) return pending.get(url);

    const request = (async () => {
      let result;
      try {
        result = await lookup(url);
      } catch (_) {
        offlineUntil = now() + OFFLINE_BACKOFF_MS;
        return BADGE_NONE;
      }
      const badge = badgeFor(result);
      // A running ingest changes soon, and a non-OK reply says nothing.
      if (result !== null && badge !== BADGE_IN_PROGRESS) {
        cache.set(url, { badge, at: now() });
      }
      return badge;
    })();
    pending.set(url, request);
    try {
      return await request;
    } finally {
      pending.delete(url);
    }
  }

  async function update(tabId, url) {
    latestUrl.set(tabId, url);
    if (!isLookupUrl(url)) {
      setBadge(tabId, BADGE_NONE);
      return;
    }
    const badge = await resolve(url);
    // The tab may have moved on while the lookup ran.
    if (latestUrl.get(tabId) !== url) return;
    setBadge(tabId, badge);
    if (badge === BADGE_IN_PROGRESS) {
      schedule(() => {
        if (latestUrl.get(tabId) === url) update(tabId, url);
      }, IN_PROGRESS_RECHECK_MS);
    }
  }

  // Call after an ingest: forget the old answer and let the server be tried
  // again even if it was offline a moment ago.
  function invalidate(url) {
    cache.delete(url);
    offlineUntil = 0;
  }

  function forgetTab(tabId) {
    latestUrl.delete(tabId);
  }

  return { update, invalidate, forgetTab };
}

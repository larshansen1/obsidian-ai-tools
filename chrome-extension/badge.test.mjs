// Tests for the "already in vault" badge logic in badge.js.
// Run with: node --test chrome-extension/badge.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  BADGE_EXISTS,
  BADGE_IN_PROGRESS,
  BADGE_NONE,
  CACHE_TTL_MS,
  IN_PROGRESS_RECHECK_MS,
  OFFLINE_BACKOFF_MS,
  badgeFor,
  createBadgeUpdater,
  isLookupUrl,
} from "./badge.js";

const URL_A = "https://example.com/article";
const URL_B = "https://example.com/other";

// Fake world: a scripted lookup, a recorded badge setter, a manual clock and
// a manual scheduler, so every test is deterministic.
function harness(responses = {}) {
  const lookups = [];
  const badges = [];
  const scheduled = [];
  const clock = { t: 1_000_000 };
  const lookup = async (url) => {
    lookups.push(url);
    const reply = typeof responses[url] === "function" ? responses[url]() : responses[url];
    if (reply instanceof Error) throw reply;
    return reply === undefined ? { exists: false, in_progress: false } : reply;
  };
  const updater = createBadgeUpdater({
    lookup,
    setBadge: (tabId, badge) => badges.push([tabId, badge]),
    now: () => clock.t,
    schedule: (fn, ms) => scheduled.push([fn, ms]),
  });
  return { updater, lookups, badges, scheduled, clock };
}

test("badge constants have the agreed text and colors", () => {
  assert.deepEqual(BADGE_NONE, { text: "", color: null });
  assert.deepEqual(BADGE_EXISTS, { text: "✓", color: "#2e7d32" });
  assert.deepEqual(BADGE_IN_PROGRESS, { text: "…", color: "#b26a00" });
  assert.equal(CACHE_TTL_MS, 60_000);
  assert.equal(OFFLINE_BACKOFF_MS, 30_000);
  assert.equal(IN_PROGRESS_RECHECK_MS, 5_000);
});

test("isLookupUrl accepts only http and https", () => {
  assert.equal(isLookupUrl("https://example.com/"), true);
  assert.equal(isLookupUrl("http://example.com/"), true);
  assert.equal(isLookupUrl("HTTPS://EXAMPLE.COM/"), true);
  assert.equal(isLookupUrl("chrome://extensions"), false);
  assert.equal(isLookupUrl("file:///tmp/a.html"), false);
  assert.equal(isLookupUrl("about:blank"), false);
  assert.equal(isLookupUrl(""), false);
  assert.equal(isLookupUrl(undefined), false);
});

test("badgeFor maps lookup replies to badges", () => {
  assert.equal(badgeFor({ exists: true, in_progress: false }), BADGE_EXISTS);
  assert.equal(badgeFor({ exists: false, in_progress: true }), BADGE_IN_PROGRESS);
  assert.equal(badgeFor({ exists: true, in_progress: true }), BADGE_IN_PROGRESS);
  assert.equal(badgeFor({ exists: false, in_progress: false }), BADGE_NONE);
  assert.equal(badgeFor({ exists: "yes" }), BADGE_NONE);
  assert.equal(badgeFor(null), BADGE_NONE);
});

test("ingested page gets the check badge", async () => {
  const h = harness({ [URL_A]: { exists: true, in_progress: false } });
  await h.updater.update(7, URL_A);
  assert.deepEqual(h.lookups, [URL_A]);
  assert.deepEqual(h.badges, [[7, BADGE_EXISTS]]);
  assert.deepEqual(h.scheduled, []);
});

test("new page gets no badge", async () => {
  const h = harness();
  await h.updater.update(7, URL_B);
  assert.deepEqual(h.lookups, [URL_B]);
  assert.deepEqual(h.badges, [[7, BADGE_NONE]]);
});

test("non-web pages clear the badge without asking the server", async () => {
  const h = harness();
  await h.updater.update(7, "chrome://newtab/");
  assert.deepEqual(h.lookups, []);
  assert.deepEqual(h.badges, [[7, BADGE_NONE]]);
});

test("answers are cached until just before the TTL", async () => {
  const h = harness({ [URL_A]: { exists: true } });
  await h.updater.update(1, URL_A);
  h.clock.t += CACHE_TTL_MS - 1;
  await h.updater.update(2, URL_A);
  assert.deepEqual(h.lookups, [URL_A]);
  assert.deepEqual(h.badges, [
    [1, BADGE_EXISTS],
    [2, BADGE_EXISTS],
  ]);
});

test("cached answer is refreshed exactly at the TTL", async () => {
  const h = harness({ [URL_A]: { exists: true } });
  await h.updater.update(1, URL_A);
  h.clock.t += CACHE_TTL_MS;
  await h.updater.update(1, URL_A);
  assert.deepEqual(h.lookups, [URL_A, URL_A]);
});

test("invalidate forces a fresh lookup after an ingest", async () => {
  let exists = false;
  const h = harness({ [URL_A]: () => ({ exists }) });
  await h.updater.update(1, URL_A);
  exists = true;
  h.updater.invalidate(URL_A);
  await h.updater.update(1, URL_A);
  assert.deepEqual(h.lookups, [URL_A, URL_A]);
  assert.deepEqual(h.badges, [
    [1, BADGE_NONE],
    [1, BADGE_EXISTS],
  ]);
});

test("server offline clears the badge and backs off until exactly the limit", async () => {
  const h = harness({ [URL_A]: new Error("connection refused") });
  await h.updater.update(1, URL_A);
  h.clock.t += OFFLINE_BACKOFF_MS - 1;
  await h.updater.update(1, URL_A);
  assert.deepEqual(h.lookups, [URL_A]);
  h.clock.t += 1;
  await h.updater.update(1, URL_A);
  assert.deepEqual(h.lookups, [URL_A, URL_A]);
  assert.deepEqual(h.badges, [
    [1, BADGE_NONE],
    [1, BADGE_NONE],
    [1, BADGE_NONE],
  ]);
});

test("offline answers are not cached once the server is back", async () => {
  let down = true;
  const h = harness({ [URL_A]: () => (down ? new Error("down") : { exists: true }) });
  await h.updater.update(1, URL_A);
  down = false;
  h.clock.t += OFFLINE_BACKOFF_MS;
  await h.updater.update(1, URL_A);
  assert.deepEqual(h.badges, [
    [1, BADGE_NONE],
    [1, BADGE_EXISTS],
  ]);
});

test("invalidate ends the offline backoff", async () => {
  let down = true;
  const h = harness({ [URL_A]: () => (down ? new Error("down") : { exists: true }) });
  await h.updater.update(1, URL_A);
  down = false;
  h.updater.invalidate(URL_A);
  await h.updater.update(1, URL_A);
  assert.deepEqual(h.lookups, [URL_A, URL_A]);
  assert.deepEqual(h.badges.at(-1), [1, BADGE_EXISTS]);
});

test("a non-OK reply shows no badge and is not cached", async () => {
  const h = harness({ [URL_A]: null });
  await h.updater.update(1, URL_A);
  await h.updater.update(1, URL_A);
  assert.deepEqual(h.lookups, [URL_A, URL_A]);
  assert.deepEqual(h.badges, [
    [1, BADGE_NONE],
    [1, BADGE_NONE],
  ]);
});

test("running ingest shows progress and re-checks until done", async () => {
  let running = true;
  const h = harness({ [URL_A]: () => (running ? { in_progress: true } : { exists: true }) });
  await h.updater.update(3, URL_A);
  assert.deepEqual(h.badges, [[3, BADGE_IN_PROGRESS]]);
  assert.equal(h.scheduled.length, 1);
  assert.equal(h.scheduled[0][1], IN_PROGRESS_RECHECK_MS);

  running = false;
  await h.scheduled[0][0]();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(h.lookups, [URL_A, URL_A]);
  assert.deepEqual(h.badges, [
    [3, BADGE_IN_PROGRESS],
    [3, BADGE_EXISTS],
  ]);
  assert.equal(h.scheduled.length, 1);
});

test("re-check is dropped when the tab has moved on", async () => {
  const h = harness({ [URL_A]: { in_progress: true } });
  await h.updater.update(3, URL_A);
  await h.updater.update(3, URL_B);
  await h.scheduled[0][0]();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(h.lookups, [URL_A, URL_B]);
});

test("a slow answer for an old URL does not overwrite the tab's badge", async () => {
  let release;
  const h = harness({
    [URL_A]: () => new Promise((r) => (release = () => r({ exists: true }))),
  });
  const slow = h.updater.update(5, URL_A);
  await h.updater.update(5, URL_B);
  release();
  await slow;
  assert.deepEqual(h.badges, [[5, BADGE_NONE]]);
});

test("simultaneous updates for one URL share a single lookup", async () => {
  const h = harness({ [URL_A]: { exists: true } });
  await Promise.all([h.updater.update(1, URL_A), h.updater.update(2, URL_A)]);
  assert.deepEqual(h.lookups, [URL_A]);
  assert.deepEqual(h.badges, [
    [1, BADGE_EXISTS],
    [2, BADGE_EXISTS],
  ]);
});

test("forgetTab drops a closed tab's pending re-check", async () => {
  const h = harness({ [URL_A]: { in_progress: true } });
  await h.updater.update(9, URL_A);
  h.updater.forgetTab(9);
  await h.scheduled[0][0]();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(h.lookups, [URL_A]);
});

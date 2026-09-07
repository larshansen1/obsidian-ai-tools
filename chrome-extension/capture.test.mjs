// Tests for the X (Twitter) thread capture logic in capture.js.
// Run with: node --test chrome-extension/capture.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  isTwitterUrl,
  extractTwitterThread,
  buildTwitterPayload,
  TWEET_SEPARATOR,
} from "./capture.js";

// Minimal fake DOM: querySelectorAll returns article stubs, each stub has
// querySelector returning element stubs with textContent / getAttribute.
function elem(text, attrs = {}) {
  return { textContent: text, getAttribute: (k) => attrs[k] ?? null, querySelector: () => null };
}

function article({ text = "", nameElText = "", time = null } = {}) {
  return {
    querySelector(sel) {
      if (sel === '[data-testid="tweetText"]') return text ? elem(text) : null;
      if (sel === '[data-testid="User-Name"]') return nameElText ? elem(nameElText) : null;
      if (sel === "time") return time ? elem("", { datetime: time }) : null;
      return null;
    },
  };
}

function docWith(articles) {
  return { querySelectorAll: () => articles };
}

test("isTwitterUrl accepts x.com and twitter.com status URLs", () => {
  assert.equal(isTwitterUrl("https://x.com/elonmusk/status/123456789"), true);
  assert.equal(isTwitterUrl("https://twitter.com/user/status/42?lang=en"), true);
  assert.equal(isTwitterUrl("https://example.com/x.com/status/1"), false);
  assert.equal(isTwitterUrl("https://x.com/home"), false);
  assert.equal(isTwitterUrl("not a url"), false);
});

test("extractTwitterThread returns ordered tweets, author, and first-tweet date", () => {
  const doc = docWith([
    article({
      text: "Tweet one",
      nameElText: "Elon Musk @elonmusk · 1h",
      time: "2026-09-07T09:00:00.000Z",
    }),
    article({ text: "Tweet two" }),
    article({ text: "   " }), // empty tweet is skipped
  ]);
  assert.deepEqual(extractTwitterThread(doc), {
    tweets: ["Tweet one", "Tweet two"],
    author: "elonmusk",
    date: "2026-09-07T09:00:00.000Z",
  });
});

test("extractTwitterThread returns {} when no tweets found", () => {
  assert.deepEqual(extractTwitterThread(docWith([])), {});
});

test("payload joins tweets with the server-side separator", () => {
  assert.deepEqual(
    buildTwitterPayload({ tweets: ["a", "b"], author: "handle", date: "2026-09-07T09:00:00.000Z" }),
    {
      captured_content: `a${TWEET_SEPARATOR}b`,
      captured_author: "handle",
      captured_date: "2026-09-07T09:00:00.000Z",
      captured_title: "@handle on X: a",
    },
  );
});

test("auth material never leaves the page", () => {
  // Secret-looking strings planted in the DOM anywhere OUTSIDE tweet text:
  // script tags, meta tags, data layers, form inputs, cookie stores.
  const secrets = [
    "Bearer eyJhbGciOiJIUzI1NiJ9.decoy-token",
    "x-csrf-token: secret-token-abc",
    "auth_token=12345abcdef; Path=/; Secure",
    "ct0=deadbeefcafe",
  ];
  // The document delegates only the tweet selectors to article stubs; every
  // other selector returns a secrets-bearing node that must never be read.
  const tweetArticles = [
    article({ text: "The only visible tweet", nameElText: "A @a · 1h" }),
  ];
  const doc = {
    querySelectorAll(sel) {
      return sel === 'article[data-testid="tweet"]'
        ? tweetArticles
        : [{ querySelector: () => elem(secrets.join("\n")) }];
    },
  };
  const payload = buildTwitterPayload(extractTwitterThread(doc));
  const serialized = JSON.stringify(payload);
  for (const secret of secrets) {
    assert.equal(serialized.includes(secret), false, `payload leaked: ${secret}`);
  }
  assert.equal(payload.captured_content, "The only visible tweet");
});

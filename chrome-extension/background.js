import { capturePage } from "./capture.js";
import { createBadgeUpdater } from "./badge.js";

const SERVER = "http://127.0.0.1:8765";

// --- "Already in vault" badge ------------------------------------------------

async function lookup(url) {
  const r = await fetch(`${SERVER}/lookup?url=${encodeURIComponent(url)}`, {
    signal: AbortSignal.timeout(2000),
  });
  return r.ok ? r.json() : null;
}

function setBadge(tabId, badge) {
  // The tab can close while a lookup is running; that is not an error.
  chrome.action.setBadgeText({ tabId, text: badge.text }).catch(() => {});
  if (badge.color) {
    chrome.action.setBadgeBackgroundColor({ tabId, color: badge.color }).catch(() => {});
    // Older Chromium builds lack setBadgeTextColor.
    chrome.action.setBadgeTextColor?.({ tabId, color: "#ffffff" })?.catch(() => {});
  }
}

const badges = createBadgeUpdater({
  lookup,
  setBadge,
  now: () => Date.now(),
  schedule: (fn, ms) => setTimeout(fn, ms),
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab) badges.update(tabId, tab.url ?? "");
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // A new URL (also in-page navigation) or a finished load, which can reset
  // the badge, both need a fresh look.
  if (changeInfo.url || changeInfo.status === "complete") {
    badges.update(tabId, tab.url ?? "");
  }
});

chrome.tabs.onRemoved.addListener((tabId) => badges.forgetTab(tabId));

// The popup reports finished ingests so the badge shows up without a reload.
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type !== "kai-refresh-badge") return;
  badges.invalidate(message.url);
  if (message.tabId != null) badges.update(message.tabId, message.url);
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "kai-ingest-page",
    title: "Ingest page with kai",
    contexts: ["page"],
  });
  chrome.contextMenus.create({
    id: "kai-ingest-link",
    title: "Ingest link with kai",
    contexts: ["link"],
  });
});

async function ingestUrl(url, capture = {}, tabId = null) {
  try {
    const r = await fetch(`${SERVER}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, ...capture }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      console.error("kai: ingest failed", data.detail ?? r.status);
      return;
    }
    badges.invalidate(url);
    if (tabId != null) badges.update(tabId, url);
  } catch (e) {
    // Server not running — user will see it the next time they open the popup.
    console.warn("kai: server unreachable", e.message);
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url =
    info.menuItemId === "kai-ingest-link" ? info.linkUrl : info.pageUrl;
  if (!url) return;

  const capture =
    info.menuItemId === "kai-ingest-page"
      ? await capturePage(tab?.id, url)
      : {};
  // Only a page ingest is about the tab itself; a link points somewhere else.
  ingestUrl(url, capture, info.menuItemId === "kai-ingest-page" ? tab?.id : null);
});

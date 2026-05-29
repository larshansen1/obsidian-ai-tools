const SERVER = "http://127.0.0.1:8765";

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

async function ingestUrl(url) {
  try {
    const r = await fetch(`${SERVER}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      console.error("kai: ingest failed", data.detail ?? r.status);
    }
  } catch (e) {
    // Server not running — user will see it the next time they open the popup.
    console.warn("kai: server unreachable", e.message);
  }
}

chrome.contextMenus.onClicked.addListener((info) => {
  const url =
    info.menuItemId === "kai-ingest-link" ? info.linkUrl : info.pageUrl;
  if (url) ingestUrl(url);
});

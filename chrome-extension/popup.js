import { capturePage } from "./capture.js";

const SERVER = "http://127.0.0.1:8765";

const dot = document.getElementById("dot");
const statusLabel = document.getElementById("status-label");
const urlEl = document.getElementById("url");
const btn = document.getElementById("btn");
const spinner = document.getElementById("spinner");
const btnLabel = document.getElementById("btn-label");
const resultEl = document.getElementById("result");
const resultTitle = document.getElementById("result-title");
const resultDetail = document.getElementById("result-detail");
const tagsEl = document.getElementById("tags");
const openLink = document.getElementById("open-link");

const POLL_INTERVAL_MS = 2000;

let currentUrl = "";
let currentTabId = null;
let updateMode = false;
let pollTimer = null;
let polling = false;

async function checkServer() {
  try {
    const r = await fetch(`${SERVER}/status`, {
      signal: AbortSignal.timeout(2000),
    });
    if (r.ok) {
      dot.className = "dot ok";
      statusLabel.textContent = "server running";
      return true;
    }
  } catch (_) {}
  dot.className = "dot err";
  statusLabel.textContent = "server offline";
  return false;
}

function isSupportedUrl(url) {
  return url.startsWith("http://") || url.startsWith("https://");
}

function stopPolling() {
  polling = false;
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function renderInProgress(elapsedSeconds) {
  const started = Math.max(0, Math.round(elapsedSeconds ?? 0));
  setLoading(true);
  btnLabel.textContent = `Ingesting… started ${started} s ago`;
  showResult(
    "info",
    "⏳ Ingesting…",
    `Started ${started} s ago — results appear here automatically.`,
  );
}

async function pollInProgress() {
  try {
    const r = await fetch(`${SERVER}/lookup?url=${encodeURIComponent(currentUrl)}`, {
      signal: AbortSignal.timeout(POLL_INTERVAL_MS),
    });
    if (!r.ok) {
      stopPolling();
      return;
    }
    const data = await r.json();
    if (data.in_progress) {
      renderInProgress(data.elapsed_seconds);
      return;
    }
    stopPolling();
    if (data.exists) {
      updateMode = true;
      btnLabel.textContent = "Update existing note";
      showResult(
        "info",
        `📄 Already in vault: ${data.title}`,
        data.file_path,
        data.tags ?? [],
        data.obsidian_url,
      );
      setLoading(false);
    } else {
      // The ingest finished without a note; fall back to idle silently.
      setLoading(false);
    }
  } catch (_) {
    // Server unreachable mid-ingest: stop asking, keep the last state.
    stopPolling();
  }
}

function startPolling() {
  if (polling) return;
  polling = true;
  pollInProgress();
  pollTimer = setInterval(pollInProgress, POLL_INTERVAL_MS);
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentUrl = tab?.url ?? "";
  currentTabId = tab?.id ?? null;
  urlEl.textContent = currentUrl || "No URL";

  const serverOk = await checkServer();
  btn.disabled = !serverOk || !isSupportedUrl(currentUrl);

  if (serverOk && isSupportedUrl(currentUrl)) {
    checkExisting();
  }
}

async function checkExisting() {
  try {
    const r = await fetch(`${SERVER}/lookup?url=${encodeURIComponent(currentUrl)}`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!r.ok) return;

    const data = await r.json();
    // An ingest is already running (started from this or another window).
    if (data.in_progress) {
      renderInProgress(data.elapsed_seconds);
      startPolling();
      return;
    }
    if (!data.exists) return;

    if (btn.disabled) return;

    updateMode = true;
    btnLabel.textContent = "Update existing note";
    showResult(
      "info",
      `📄 Already in vault: ${data.title}`,
      data.file_path,
      data.tags ?? [],
      data.obsidian_url,
    );
  } catch (_) {
    // Server offline: keep the idle state; the dot already shows it.
  }
}

function renderTags(tags) {
  tagsEl.replaceChildren(
    ...tags.map((t) => {
      const span = document.createElement("span");
      span.className = "tag";
      span.textContent = t;
      return span;
    }),
  );
}

function showResult(type, title, detail, tags = [], obsidianUrl = null) {
  resultEl.className = `result visible ${type}`;
  resultTitle.textContent = title;
  resultDetail.textContent = detail;
  renderTags(tags);
  openLink.className = obsidianUrl ? "open-link visible" : "open-link";
  openLink.dataset.url = obsidianUrl ?? "";
}

openLink.addEventListener("click", () => {
  if (openLink.dataset.url && currentTabId !== null) {
    chrome.tabs.update(currentTabId, { url: openLink.dataset.url });
  }
});

function setLoading(loading) {
  btn.disabled = loading;
  spinner.style.display = loading ? "block" : "none";
  if (loading) {
    btnLabel.textContent = "Ingesting…";
  } else {
    btnLabel.textContent = updateMode ? "Update existing note" : "Ingest into Obsidian";
  }
}

btn.addEventListener("click", async () => {
  stopPolling();
  setLoading(true);
  resultEl.className = "result";

  try {
    const capture = await capturePage(currentTabId, currentUrl);
    const r = await fetch(`${SERVER}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, update: updateMode, ...capture }),
    });

    if (r.status === 409) {
      // Another window is already ingesting this URL; follow its progress.
      renderInProgress(0);
      startPolling();
      return;
    }

    const data = await r.json();

    if (r.ok && data.status === "exists") {
      updateMode = true;
      showResult(
        "info",
        `📄 Already in vault: ${data.title}`,
        data.file_path,
        data.tags,
        data.obsidian_url,
      );
    } else if (r.ok) {
      updateMode = false;
      showResult("success", `✓ ${data.title}`, data.file_path, data.tags, data.obsidian_url);
    } else {
      showResult("error", "Ingestion failed", data.detail ?? "Unknown error");
    }
  } catch (e) {
    showResult(
      "error",
      "Connection failed",
      "Is kai serve running?  (kai serve --port 8765)",
    );
  } finally {
    if (!polling) setLoading(false);
  }
});

init();

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

let currentUrl = "";

// ── Server health check ──────────────────────────────────────────────────────

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

// ── Initialise popup ─────────────────────────────────────────────────────────

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentUrl = tab?.url ?? "";
  urlEl.textContent = currentUrl || "No URL";

  const serverOk = await checkServer();
  btn.disabled = !serverOk || !isSupportedUrl(currentUrl);
}

// ── Result display ───────────────────────────────────────────────────────────

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

function showResult(type, title, detail, tags = []) {
  resultEl.className = `result visible ${type}`;
  resultTitle.textContent = title;
  resultDetail.textContent = detail;
  renderTags(tags);
}

function setLoading(loading) {
  btn.disabled = loading;
  spinner.style.display = loading ? "block" : "none";
  btnLabel.textContent = loading ? "Ingesting…" : "Ingest into Obsidian";
}

// ── Ingest ───────────────────────────────────────────────────────────────────

btn.addEventListener("click", async () => {
  setLoading(true);
  resultEl.className = "result";

  try {
    const r = await fetch(`${SERVER}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl }),
    });

    const data = await r.json();

    if (r.ok) {
      showResult("success", `✓ ${data.title}`, data.file_path, data.tags);
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
    setLoading(false);
  }
});

init();

const CHAT_HOSTS = new Set(["chatgpt.com", "chat.openai.com", "claude.ai"]);
const TWITTER_HOSTS = new Set(["x.com", "twitter.com"]);

// Keep in sync with _TWEET_SEPARATOR in src/obsidian_ai_tools/providers/x.py,
// which splits captured_thread text back into individual tweets server-side.
export const TWEET_SEPARATOR = "\n\n---\n\n";

export function isChatUrl(url) {
  try {
    return CHAT_HOSTS.has(new URL(url).hostname);
  } catch (_) {
    return false;
  }
}

function extractPageContent() {
  const content = document.querySelector("main")?.innerText ?? document.body.innerText;
  return {
    captured_content: content.trim(),
    captured_title: document.title,
  };
}

export async function captureChatPage(tabId, url) {
  if (!tabId || !isChatUrl(url)) return {};

  try {
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractPageContent,
    });
    return injection.result?.captured_content ? injection.result : {};
  } catch (e) {
    console.warn("kai: could not capture chat page", e.message);
    return {};
  }
}

export function isTwitterUrl(url) {
  try {
    const u = new URL(url);
    return TWITTER_HOSTS.has(u.hostname) && /^\/[^/]+\/status\/\d+/.test(u.pathname);
  } catch (_) {
    return false;
  }
}

// Runs in the page context via chrome.scripting.executeScript, so it must be
// self-contained: no references to module-scope bindings. Reads only tweet
// text, author handle and tweet timestamp from the DOM. Never touches cookies,
// tokens, or auth headers — nothing secret leaves the page.
export function extractTwitterThread(doc = document) {
  const articles = Array.from(doc.querySelectorAll('article[data-testid="tweet"]'));
  const tweets = [];
  let author = null;
  let date = null;
  for (const article of articles) {
    const textEl = article.querySelector('[data-testid="tweetText"]');
    const text = textEl?.textContent?.trim();
    if (!text) continue;
    tweets.push(text);
    if (author === null && date === null) {
      const nameEl = article.querySelector('[data-testid="User-Name"]');
      const match = nameEl ? /@([A-Za-z0-9_]+)/.exec(nameEl.textContent) : null;
      if (match) author = match[1];
      const timeEl = article.querySelector("time");
      date = timeEl?.getAttribute("datetime") ?? null;
    }
  }
  if (tweets.length === 0) return {};
  return { tweets, author, date };
}

export function buildTwitterPayload({ tweets, author, date }) {
  const first = tweets[0].slice(0, 100);
  return {
    captured_content: tweets.join(TWEET_SEPARATOR),
    captured_author: author,
    captured_date: date,
    captured_title: author ? `@${author} on X: ${first}` : `X thread: ${first}`,
  };
}

export async function captureTwitterPage(tabId, url) {
  if (!tabId || !isTwitterUrl(url)) return {};

  try {
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractTwitterThread,
    });
    const extracted = injection.result;
    if (!extracted || extracted.tweets?.length === 0) return {};
    return buildTwitterPayload(extracted);
  } catch (e) {
    console.warn("kai: could not capture twitter page", e.message);
    return {};
  }
}

export async function capturePage(tabId, url) {
  if (isChatUrl(url)) return captureChatPage(tabId, url);
  if (isTwitterUrl(url)) return captureTwitterPage(tabId, url);
  return {};
}

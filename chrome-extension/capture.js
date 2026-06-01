const CHAT_HOSTS = new Set(["chatgpt.com", "chat.openai.com", "claude.ai"]);

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

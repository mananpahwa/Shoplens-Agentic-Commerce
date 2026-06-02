// ShopLens Background Service Worker
const MODAL_ENDPOINT = "https://manaan-pahwa--shoplens-backend-analyze.modal.run";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[ShopLens] Message received: ' + message.type);
  // Guard: sender.tab is undefined if message came from an extension page, not a content script
  if (message.type === "ANALYZE_FRAME" && sender.tab && sender.tab.id) {
    handleAnalysis(message.imageB64, sender.tab.id);
  }
  return true; // Required to keep message channel open for async response
});

function safeSendMessage(tabId, payload) {
  chrome.tabs.sendMessage(tabId, payload).catch((err) => {
    // "Receiving end does not exist" is expected if the tab navigated away — suppress it
    console.log('[ShopLens] sendMessage suppressed:', err.message);
  });
}

async function handleAnalysis(imageB64, tabId) {
  console.log('[ShopLens] imageB64 length received: ' + (imageB64 ? imageB64.length : 'undefined/null'));

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 20000);

    console.log('[ShopLens] Fetching Modal endpoint...');

    const response = await fetch(MODAL_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_b64: imageB64 }),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    console.log('[ShopLens] Modal response status: ' + response.status);

    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }

    const data = await response.json();
    console.log('[ShopLens] Modal response body:', JSON.stringify(data));
    const products = data.products || [];

    safeSendMessage(tabId, {
      type: "PRODUCTS_RESULT",
      products: products.slice(0, 3)
    });

  } catch (error) {
    console.log("[ShopLens] Fetch error:", error.message);
    safeSendMessage(tabId, {
      type: "PRODUCTS_RESULT",
      products: [],
      error: error.message
    });
  }
}

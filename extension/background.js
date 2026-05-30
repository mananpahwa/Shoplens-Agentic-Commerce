// ShopLens Background Service Worker
const MODAL_ENDPOINT = "https://manaan-pahwa--shoplens-backend-analyze.modal.run";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[ShopLens] Message received: ' + message.type);
  if (message.type === "ANALYZE_FRAME") {
    handleAnalysis(message.imageB64, sender.tab.id);
  }
  return true; // Required to keep message channel open for async response
});

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
    const products = data.products || [];

    chrome.tabs.sendMessage(tabId, {
      type: "PRODUCTS_RESULT",
      products: products.slice(0, 3)
    });

  } catch (error) {
    console.log("[ShopLens] Fetch error:", error.message);
    chrome.tabs.sendMessage(tabId, {
      type: "PRODUCTS_RESULT",
      products: [],
      error: error.message
    });
  }
}

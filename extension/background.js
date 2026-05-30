// ShopLens Background Service Worker
// IMPORTANT: Replace MODAL_ENDPOINT with your actual Modal URL after deploy
const MODAL_ENDPOINT = "https://manaan-pahwa--shoplens-backend-analyze.modal.run";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ANALYZE_FRAME") {
    handleAnalysis(message.imageB64, sender.tab.id);
  }
  return true; // Required to keep message channel open for async response
});

async function handleAnalysis(imageB64, tabId) {
  try {
    // AbortController for timeout — 20 seconds to handle Modal cold starts
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 20000);

    const response = await fetch(MODAL_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_b64: imageB64 }),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

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

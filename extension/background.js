// ShopLens Background Service Worker
const MODAL_ENDPOINT = "https://manaan-pahwa--shoplens-backend-shoplensanalyzer-analyze.modal.run";

console.log('[ShopLens] Service worker started');

// Keep-alive: chrome.alarms fires every 20s, preventing Chrome from
// killing the SW while we wait for Modal's cold start response.
chrome.alarms.create('shoplens-keepalive', { periodInMinutes: 0.3 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'shoplens-keepalive') {
    console.log('[ShopLens] Keep-alive ping');
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log('[ShopLens] Message received: ' + message.type);

  if (message.type === 'ANALYZE_FRAME' && sender.tab && sender.tab.id) {
    // Acknowledge immediately — closes the message channel cleanly.
    // Results are sent back separately via chrome.tabs.sendMessage.
    sendResponse({ received: true });
    handleAnalysis(message.imageB64, sender.tab.id);
  }
  // Do NOT return true — sendResponse already called synchronously.
});

async function handleAnalysis(imageB64, tabId) {
  console.log('[ShopLens] imageB64 length received: ' + (imageB64 ? imageB64.length : 'undefined/null'));

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 90000); // 90s for cold start

    console.log('[ShopLens] Fetching Modal endpoint...');

    const response = await fetch(MODAL_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: imageB64 }),
      signal: controller.signal
    });

    clearTimeout(timeoutId);
    console.log('[ShopLens] Modal response status: ' + response.status);

    if (!response.ok) throw new Error('HTTP ' + response.status);

    const data = await response.json();
    console.log('[ShopLens] Modal response body:', JSON.stringify(data));
    const products = data.products || [];

    safeSendMessage(tabId, {
      type: 'PRODUCTS_RESULT',
      products: products.slice(0, 3)
    });

  } catch (error) {
    console.log('[ShopLens] Fetch error:', error.message);
    safeSendMessage(tabId, {
      type: 'PRODUCTS_RESULT',
      products: [],
      error: error.message
    });
  }
}

function safeSendMessage(tabId, payload) {
  chrome.tabs.sendMessage(tabId, payload).catch((err) => {
    console.log('[ShopLens] sendMessage suppressed:', err.message);
  });
}

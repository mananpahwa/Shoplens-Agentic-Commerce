// ShopLens Background Service Worker
const MODAL_ENDPOINT = "https://manaan-pahwa--shoplens-backend-analyze.modal.run";

// Use persistent port connections (chrome.runtime.connect) instead of sendMessage.
// Ports keep the service worker alive for the full duration of the Modal fetch,
// preventing Chrome from killing it mid-request during cold starts.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'shoplens-analysis') return;

  port.onMessage.addListener(async (message) => {
    if (message.type !== 'ANALYZE_FRAME') return;

    const imageB64 = message.imageB64;
    console.log('[ShopLens] Message received: ANALYZE_FRAME');
    console.log('[ShopLens] imageB64 length received: ' + (imageB64 ? imageB64.length : 'undefined/null'));

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 55000); // 55s to cover cold starts

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

      port.postMessage({ type: 'PRODUCTS_RESULT', products: products.slice(0, 3) });

    } catch (error) {
      console.log('[ShopLens] Fetch error:', error.message);
      port.postMessage({ type: 'PRODUCTS_RESULT', products: [], error: error.message });
    }
  });
});

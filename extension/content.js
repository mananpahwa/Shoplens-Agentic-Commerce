// ShopLens Content Script — injected into YouTube Shorts pages

// ─── PART A: STATE ───────────────────────────────────────────────────────────
let isLoading = false;
let panelVisible = false;
let dismissedThisSession = [];

// ─── PART G: UTILITY FUNCTIONS ───────────────────────────────────────────────

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function showToast(message) {
  const existing = document.querySelector('.shoplens-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = 'shoplens-toast';
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    if (toast.parentNode) toast.remove();
  }, 2600);
}

function resetButton() {
  isLoading = false;
  const btn = document.getElementById('shoplens-btn');
  if (btn) btn.classList.remove('loading');
}

function closePanel() {
  const panel = document.querySelector('.shoplens-panel');
  if (!panel) return;
  panel.classList.remove('visible');
  setTimeout(() => {
    if (panel.parentNode) panel.remove();
    panelVisible = false;
  }, 300);
}

// ─── PART F: RENDER OVERLAY PANEL ────────────────────────────────────────────

function renderPanel(products) {
  const existing = document.querySelector('.shoplens-panel');
  if (existing) existing.remove();

  panelVisible = true;

  const panel = document.createElement('div');
  panel.className = 'shoplens-panel';

  const header = document.createElement('div');
  header.className = 'shoplens-panel-header';
  header.innerHTML = `
    <span class="shoplens-panel-title">ShopLens</span>
    <button class="shoplens-close-btn">✕</button>
  `;
  header.querySelector('.shoplens-close-btn').addEventListener('click', closePanel);

  const list = document.createElement('div');
  list.className = 'shoplens-products-list';

  const maxProducts = products.slice(0, 3);
  maxProducts.forEach((product) => {
    const card = document.createElement('div');
    card.className = 'shoplens-product-card';
    card.innerHTML = `
      <img
        class="shoplens-product-thumb"
        src="${escapeHtml(product.thumbnail)}"
        onerror="this.style.background='#1a1a2e';this.src=''"
        alt=""
      />
      <div class="shoplens-product-info">
        <div class="shoplens-product-title">${escapeHtml(product.title)}</div>
        <div class="shoplens-product-price">${escapeHtml(product.price || '')}</div>
        <div class="shoplens-product-source">${escapeHtml(product.source || '')}</div>
      </div>
    `;
    card.addEventListener('click', () => {
      if (product.link) window.open(product.link, '_blank');
    });
    list.appendChild(card);
  });

  const shopBtn = document.createElement('button');
  shopBtn.className = 'shoplens-shop-btn';
  shopBtn.textContent = 'Shop Now →';
  shopBtn.addEventListener('click', () => {
    if (maxProducts.length > 0 && maxProducts[0].link) {
      window.open(maxProducts[0].link, '_blank');
    }
  });

  panel.appendChild(header);
  panel.appendChild(list);
  panel.appendChild(shopBtn);
  document.body.appendChild(panel);

  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      panel.classList.add('visible');
    });
  });

  setTimeout(() => closePanel(), 45000);
}

// ─── PART D: FRAME CAPTURE AND SEND ──────────────────────────────────────────

// Captures a JPEG frame from the video element as a pure base64 string.
// Uses ImageCapture API (Method 1) to bypass cross-origin canvas taint on
// YouTube videos (served from googlevideo.com). Falls back to direct canvas
// draw (Method 2) for browsers without ImageCapture support.
async function captureFrame(video) {
  // Method 1: ImageCapture API — grabs an ImageBitmap from the video stream.
  // ImageBitmap obtained this way is NOT tainted, so toDataURL() works.
  if ('ImageCapture' in window) {
    let stream = null;
    let track = null;
    try {
      stream = video.captureStream();
      track = stream.getVideoTracks()[0];
      if (track) {
        const imageCapture = new ImageCapture(track);
        const bitmap = await imageCapture.grabFrame();

        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(bitmap, 0, 0);
        bitmap.close();

        const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
        console.log('[ShopLens] Captured via ImageCapture API');
        return dataUrl.split(',')[1];
      }
    } catch (e) {
      console.log('[ShopLens] ImageCapture failed:', e.message);
    } finally {
      if (track) track.stop();
      if (stream) stream.getTracks().forEach(t => t.stop());
    }
  }

  // Method 2: Direct canvas draw — works if the browser doesn't enforce taint
  // for this context (some Chromium builds / extension contexts allow it).
  try {
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
    console.log('[ShopLens] Captured via direct canvas draw');
    return dataUrl.split(',')[1];
  } catch (e) {
    console.log('[ShopLens] Direct canvas draw also failed:', e.message);
    return null;
  }
}

async function triggerAnalysis() {
  console.log('[ShopLens] Button clicked');

  if (isLoading) return;

  isLoading = true;
  const btn = document.getElementById('shoplens-btn');
  if (btn) btn.classList.add('loading');

  const video = document.querySelector('video');
  if (!video) {
    showToast('No video found — try again');
    resetButton();
    return;
  }

  console.log('[ShopLens] Video found: ' + video.videoWidth + 'x' + video.videoHeight);

  const imageB64 = await captureFrame(video);

  if (!imageB64 || imageB64.length < 100) {
    console.log('[ShopLens] imageB64 is empty or too short — all capture methods failed');
    showToast('Could not capture frame — try again');
    resetButton();
    return;
  }

  console.log('[ShopLens] imageB64 length: ' + imageB64.length);
  console.log('[ShopLens] Sending to background...');

  // Use a persistent port connection so the service worker stays alive
  // for the full duration of the Modal fetch (cold starts can take 30-50s).
  let port;
  try {
    port = chrome.runtime.connect({ name: 'shoplens-analysis' });
  } catch (err) {
    console.log('[ShopLens] Connect error:', err.message);
    showToast('Try again in a moment');
    resetButton();
    return;
  }

  port.postMessage({ type: 'ANALYZE_FRAME', imageB64 });
  console.log('[ShopLens] Message sent via port');

  port.onMessage.addListener((message) => {
    if (message.type === 'PRODUCTS_RESULT') {
      port.disconnect();
      resetButton();
      if (!message.products || message.products.length === 0) {
        showToast('Nothing found — try a clearer frame');
        return;
      }
      renderPanel(message.products);
    }
  });

  port.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    if (err) {
      console.log('[ShopLens] Port disconnected with error:', err.message);
      showToast('Try again in a moment');
      resetButton();
    }
  });
}

// ─── PART B: BUTTON INJECTION ─────────────────────────────────────────────────

function injectButton() {
  if (document.getElementById('shoplens-btn')) return;

  const btn = document.createElement('div');
  btn.id = 'shoplens-btn';
  btn.className = 'shoplens-btn';
  btn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z"/>
      <line x1="3" y1="6" x2="21" y2="6"/>
      <path d="M16 10a4 4 0 01-8 0"/>
    </svg>
  `;
  btn.addEventListener('click', triggerAnalysis);
  document.body.appendChild(btn);
}

function removeButton() {
  const btn = document.getElementById('shoplens-btn');
  if (btn) btn.remove();
}

// ─── PART C: YOUTUBE SPA NAVIGATION DETECTION ────────────────────────────────

function handleNavigation() {
  if (location.href.includes('/shorts/')) {
    setTimeout(() => injectButton(), 1500);
  } else {
    removeButton();
    closePanel();
  }
}

let lastUrl = location.href;
const observer = new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    handleNavigation();
  }
});
observer.observe(document.body, { subtree: true, childList: true });

// Initial injection if already on a Shorts URL
if (location.href.includes('/shorts/')) {
  injectButton();
}

import os
import base64
import io
import requests
import modal
from PIL import Image
from urllib.parse import quote_plus

app = modal.App("shoplens-backend")

image = (
    modal.Image.debian_slim()
    .apt_install("libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "pillow", "requests",
        "fastapi[standard]", "python-multipart",
    )
)

INDIAN_DOMAINS = [
    "myntra.com", "flipkart.com", "amazon.in",
    "meesho.com", "ajio.com", "nykaafashion.com", "nykaa.com",
    "snapdeal.com", "tatacliq.com", "bewakoof.com",
    "reliancetrends.com", "westside.com", "limeroad.com",
    "pantaloons.com", "maxfashion.in", "biba.in",
    "fabindia.com", "libas.in", "aurelia.in",
    "urbanic.com", "virgio.com", "global-desi.com",
    "zara.com", "hm.com", "uniqlo.com",
    "puma.com", "adidas.co.in", "reebok.in",
]

MERCHANT_SEARCH_URLS = {
    "myntra":          "https://www.myntra.com/{}",
    "flipkart":        "https://www.flipkart.com/search?q={}",
    "amazon":          "https://www.amazon.in/s?k={}",
    "meesho":          "https://www.meesho.com/search?q={}",
    "ajio":            "https://www.ajio.com/search/?text={}",
    "nykaa":           "https://www.nykaafashion.com/search?q={}",
    "snapdeal":        "https://www.snapdeal.com/search?keyword={}",
    "tata cliq":       "https://www.tatacliq.com/search/?q={}",
    "tatacliq":        "https://www.tatacliq.com/search/?q={}",
    "westside":        "https://www.westside.com/search?type=product&q={}",
    "bewakoof":        "https://www.bewakoof.com/search/{}",
    "biba":            "https://www.biba.in/search?q={}",
    "fabindia":        "https://www.fabindia.com/search?q={}",
    "libas":           "https://www.libas.in/search?q={}",
    "h&m":             "https://www2.hm.com/en_in/search-results.html?q={}",
    "zara":            "https://www.zara.com/in/en/search?searchTerm={}",
    "pantaloons":      "https://www.pantaloons.com/search?q={}",
    "max fashion":     "https://www.maxfashion.in/in/en/search?text={}",
    "reliance trends": "https://www.reliancetrends.com/search?q={}",
    "urbanic":         "https://www.urbanic.com/search?q={}",
}


@app.cls(image=image, secrets=[modal.Secret.from_dotenv()])
class ShopLensAnalyzer:

    def upload_image(self, image_bytes):
        image_url = None
        log = []

        for host, fn in [
            ("catbox",    self._upload_catbox),
            ("tmpfiles",  self._upload_tmpfiles),
            ("uguu",      self._upload_uguu),
            ("0x0",       self._upload_0x0),
        ]:
            try:
                image_url = fn(image_bytes)
                log.append(f"{host}=ok")
                if image_url:
                    break
            except Exception as e:
                log.append(f"{host}=err:{str(e)[:30]}")

        print(f"[ShopLens] Upload: {log} → {image_url or 'FAILED'}")
        return image_url

    def _upload_catbox(self, b):
        r = requests.post("https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": ("image.jpg", b, "image/jpeg")}, timeout=30)
        url = r.text.strip()
        return url if r.status_code == 200 and url.startswith("https://") else None

    def _upload_tmpfiles(self, b):
        r = requests.post("https://tmpfiles.org/api/v1/upload",
            files={"file": ("image.jpg", b, "image/jpeg")}, timeout=30)
        if r.status_code == 200:
            raw = r.json().get("data", {}).get("url", "")
            return raw.replace("tmpfiles.org/", "tmpfiles.org/dl/") if raw else None
        return None

    def _upload_uguu(self, b):
        r = requests.post("https://uguu.se/upload.php",
            files={"files[]": ("image.jpg", b, "image/jpeg")}, timeout=30)
        if r.status_code == 200:
            files = r.json().get("files", [])
            return files[0]["url"] if files else None
        return None

    def _upload_0x0(self, b):
        r = requests.post("https://0x0.st",
            files={"file": ("image.jpg", b, "image/jpeg")}, timeout=30)
        url = r.text.strip()
        return url if r.status_code == 200 and url.startswith("http") else None

    def is_indian(self, url):
        return any(d in url for d in INDIAN_DOMAINS)

    def format_product(self, p, thumbnail=None):
        title = (p.get("title") or "")[:60]
        link = p.get("link") or p.get("product_link", "")
        price = str(p.get("price", "") or "")
        source = p.get("source", "")
        if not source:
            from urllib.parse import urlparse
            source = urlparse(link).netloc.replace("www.", "")
        return {
            "title": title,
            "link": link,
            "price": price,
            "source": source,
            "thumbnail": thumbnail or p.get("thumbnail", ""),
        }

    def google_lens(self, image_url, serpapi_key):
        r = requests.get("https://serpapi.com/search", params={
            "engine": "google_lens",
            "url": image_url,
            "gl": "in", "hl": "en",
            "api_key": serpapi_key,
        }, timeout=30)
        data = r.json()
        shopping = data.get("shopping_results", [])
        visual = data.get("visual_matches", [])
        print(f"[ShopLens] Lens raw: {len(shopping)} shopping, {len(visual)} visual")
        return shopping, visual

    def google_shopping(self, query, serpapi_key):
        r = requests.get("https://serpapi.com/search", params={
            "engine": "google_shopping",
            "q": query,
            "gl": "in", "hl": "en",
            "num": 20,
            "api_key": serpapi_key,
        }, timeout=30)
        results = r.json().get("shopping_results", [])
        print(f"[ShopLens] Shopping '{query}': {len(results)} results")
        return results

    def shopping_query_from_visual(self, visual_matches):
        """Extract a product-like search query from Lens visual match titles."""
        skip_words = {
            "buy", "shop", "online", "india", "price", "style", "wear",
            "outfit", "look", "fashion", "trend", "celebrity", "actor",
            "actress", "bollywood", "photo", "image", "picture",
        }
        for match in visual_matches[:8]:
            title = (match.get("title") or "").strip()
            if not title or len(title) < 5:
                continue
            # Skip non-product titles (news/blog/celebrity)
            lower = title.lower()
            if any(w in lower for w in ["–", " in ", " at ", " of ", " by ", " and "]):
                continue
            # Looks product-like — use it
            words = [w for w in title.split() if w.lower() not in skip_words]
            if len(words) >= 2:
                print(f"[ShopLens] Using visual title as query: '{title}'")
                return " ".join(words[:6])
        # Fallback: use the raw first title
        if visual_matches:
            return (visual_matches[0].get("title") or "")[:50]
        return ""

    @modal.fastapi_endpoint(method="POST")
    def analyze(self, item: dict):
        try:
            image_b64 = item.get("image_b64", "")
            if not image_b64:
                return {"products": [], "error": "no image provided"}

            img_bytes = base64.b64decode(image_b64)
            pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            pil.thumbnail((1280, 1280))
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()

            image_url = self.upload_image(img_bytes)
            if not image_url:
                return {"products": [], "error": "image upload failed"}

            serpapi_key = os.environ["SERPAPI_KEY"]

            # Step 1 — Google Lens visual search
            lens_shopping, visual_matches = self.google_lens(image_url, serpapi_key)

            products = []
            seen = set()

            # Prefer Lens shopping results from Indian domains (have price + thumbnail)
            for p in lens_shopping:
                link = p.get("link") or p.get("product_link", "")
                if not link or link in seen or not self.is_indian(link):
                    continue
                seen.add(link)
                products.append(self.format_product(p))

            # Then Lens visual matches from Indian domains
            for p in visual_matches:
                link = p.get("link", "")
                if not link or link in seen or not self.is_indian(link):
                    continue
                seen.add(link)
                products.append(self.format_product(p))

            print(f"[ShopLens] After Lens filter: {len(products)} Indian results")

            # Step 2 — If Lens didn't return enough Indian results, fall back to
            # Google Shopping using the visual match title as the search query.
            # Lens understands the image visually; we use its title as a text query
            # for Indian store search so we always return something relevant.
            if len(products) < 4:
                query = self.shopping_query_from_visual(visual_matches)
                if not query:
                    query = "fashion clothing"
                print(f"[ShopLens] Falling back to Shopping with: '{query}'")

                shopping_results = self.google_shopping(query, serpapi_key)
                q_enc = quote_plus(query)

                for p in shopping_results:
                    link = p.get("link") or p.get("product_link", "")
                    source_name = (p.get("source") or "").lower().strip()

                    # Replace google.com catalog URLs with real merchant search URLs
                    if not link or "google.com" in link:
                        link = ""
                        for key, url_tpl in MERCHANT_SEARCH_URLS.items():
                            if key in source_name:
                                link = url_tpl.format(q_enc)
                                break

                    if not link or not link.startswith("http"):
                        continue
                    if link in seen:
                        continue
                    if not self.is_indian(link):
                        continue

                    seen.add(link)
                    products.append(self.format_product(p))

                print(f"[ShopLens] After Shopping fallback: {len(products)} total")

            return {"products": products[:10]}

        except Exception as e:
            import traceback
            print(f"[ShopLens] Exception:\n{traceback.format_exc()}")
            return {"products": [], "error": str(e)[:200]}

    @modal.fastapi_endpoint(method="GET")
    def health(self):
        return {"status": "ok", "service": "shoplens-backend"}

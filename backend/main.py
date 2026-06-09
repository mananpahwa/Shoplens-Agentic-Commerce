import os
import base64
import io
import requests
import modal
from PIL import Image

app = modal.App("shoplens-backend")

image = (
    modal.Image.debian_slim()
    .apt_install("libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "ultralytics", "pillow", "requests",
        "fastapi[standard]", "python-multipart", "transformers"
    )
    .run_commands(
        "python -c 'from ultralytics import YOLO; YOLO(\"yolo11n.pt\")'",
        "python -c 'from transformers import CLIPModel, CLIPProcessor; "
        "CLIPModel.from_pretrained(\"patrickjohncyh/fashion-clip\"); "
        "CLIPProcessor.from_pretrained(\"patrickjohncyh/fashion-clip\")'"
    )
)

# Maps the SerpApi `source` field from Shopping results to a merchant search URL.
# Google Shopping returns google.com catalog URLs in `link`; we replace them
# with a real merchant search URL constructed from the query + source name.
MERCHANT_SEARCH_URLS = {
    "myntra":           "https://www.myntra.com/{}",
    "flipkart":         "https://www.flipkart.com/search?q={}",
    "amazon":           "https://www.amazon.in/s?k={}",
    "meesho":           "https://www.meesho.com/search?q={}",
    "ajio":             "https://www.ajio.com/search/?text={}",
    "nykaa":            "https://www.nykaafashion.com/search?q={}",
    "snapdeal":         "https://www.snapdeal.com/search?keyword={}",
    "tata cliq":        "https://www.tatacliq.com/search/?q={}",
    "tatacliq":         "https://www.tatacliq.com/search/?q={}",
    "westside":         "https://www.westside.com/search?type=product&q={}",
    "bewakoof":         "https://www.bewakoof.com/search/{}",
    "biba":             "https://www.biba.in/search?q={}",
    "fabindia":         "https://www.fabindia.com/search?q={}",
    "libas":            "https://www.libas.in/search?q={}",
    "aurelia":          "https://www.aurelia.in/search?q={}",
    "h&m":              "https://www2.hm.com/en_in/search-results.html?q={}",
    "zara":             "https://www.zara.com/in/en/search?searchTerm={}",
    "limeroad":         "https://www.limeroad.com/search?q={}",
    "pantaloons":       "https://www.pantaloons.com/search?q={}",
    "max fashion":      "https://www.maxfashion.in/in/en/search?text={}",
    "maxfashion":       "https://www.maxfashion.in/in/en/search?text={}",
    "global desi":      "https://www.global-desi.com/search?q={}",
    "virgio":           "https://www.virgio.com/search?q={}",
    "reliance trends":  "https://www.reliancetrends.com/search?q={}",
    "urbanic":          "https://www.urbanic.com/search?q={}",
}

# Known Indian + trusted international selling platforms → score boost
INDIAN_BOOST = {
    "flipkart.com": 3, "myntra.com": 3, "amazon.in": 3,
    "meesho.com": 2, "ajio.com": 2, "nykaa.com": 2, "nykaafashion.com": 2,
    "snapdeal.com": 1, "tatacliq.com": 1, "bewakoof.com": 1,
    "reliancetrends.com": 1, "westside.com": 1, "limeroad.com": 1,
    "pantaloons.com": 1, "maxfashion.in": 1, "biba.in": 1,
    "fabindia.com": 1, "libas.in": 1, "aurelia.in": 1,
    "urbanic.com": 1, "virgio.com": 1, "global-desi.com": 1,
    "zara.com": 1, "hm.com": 1, "uniqlo.com": 1, "mango.com": 1,
}

# Hard-blocked domains — never useful to a buyer in India
BLOCKED_DOMAINS = {
    # Social media
    "reddit.com", "instagram.com", "facebook.com", "twitter.com",
    "x.com", "tiktok.com", "pinterest.com", "youtube.com",
    "snapchat.com", "tumblr.com", "linkedin.com",
    # Western-only e-commerce (no Indian store / INR pricing)
    "amazon.com", "amazon.ca", "amazon.co.uk", "amazon.de",
    "amazon.fr", "amazon.co.jp", "amazon.com.au",
    "etsy.com", "ebay.com", "ebay.co.uk",
    "walmart.com", "target.com", "nordstrom.com", "macys.com",
    "asos.com", "boohoo.com", "shein.com",
    # Google's own pages
    "google.com",
    # International news / editorial
    "wikipedia.org", "vogue.com", "harpersbazaar.com",
    "elle.com", "cosmopolitan.com",
    # Indian news (Google Lens often finds these when it recognises a person)
    "indiatoday.in", "hindustantimes.com", "ndtv.com", "timesofindia.com",
    "news18.com", "firstpost.com", "thehindu.com", "indianexpress.com",
    "livemint.com", "businessinsider.in", "economictimes.com",
    # Indian celebrity / entertainment gossip — Lens finds these for any known face
    "mensxp.com", "scoopwhoop.com", "storypick.com", "vagabomb.com",
    "bollywoodshaadis.com", "pinkvilla.com", "filmfare.com", "koimoi.com",
    "bollywoodlife.com", "spotboye.com", "desimartini.com", "glamsham.com",
    "odishatv.in", "jagranjunction.com", "navbharattimes.com",
    "scroll.in", "thequint.com", "thewire.in", "theprint.in",
    "mid-day.com", "dnaindia.com", "deccanherald.com",
}

# URL path patterns that indicate non-shopping pages
NON_SHOPPING_PATTERNS = [
    "/article/", "/articles/", "/news/", "/blog/", "/blogs/",
    "/editorial/", "/magazine/", "/watch?", "/post/",
    "/story/", "/stories/", "/review/", "/reviews/",
]

# Price strings that confirm Indian rupee → +2 boost
INR_MARKERS = {"₹", "Rs.", "Rs ", "INR"}

# Price strings that indicate foreign currency → hard block
FOREIGN_CURRENCY_MARKERS = {"US$", "USD", "CA$", "CAD", "AU$", "AUD", "£", "GBP", "€", "EUR", "US $", "C$"}

GARMENT_LABELS = [
    "kurta", "salwar kameez", "saree", "lehenga", "anarkali",
    "dress", "jeans", "trousers", "shirt", "t-shirt",
    "jacket", "blazer", "skirt", "shorts", "ethnic wear",
]

# Garments that are unambiguously men's — append "men" to search query and
# suppress alt-query (prevents women's kurta results for a men's shirt).
MENS_GARMENTS = {"shirt", "t-shirt", "trousers", "jeans", "blazer", "shorts", "jacket"}

# Garments that are unambiguously women's — append "women" to search query.
WOMENS_GARMENTS = {"dress", "saree", "lehenga", "anarkali", "salwar kameez", "skirt"}
COLOR_PALETTE = {
    "red": [210, 50, 50], "pink": [230, 100, 150], "orange": [230, 130, 50],
    "yellow": [220, 200, 60], "green": [60, 150, 60], "blue": [50, 100, 200],
    "navy": [30, 50, 120], "purple": [130, 60, 190], "white": [230, 230, 230],
    "black": [30, 30, 30], "grey": [128, 128, 128], "beige": [210, 190, 150],
    "brown": [130, 80, 50], "maroon": [128, 0, 50],
}


@app.cls(image=image, gpu="T4", secrets=[modal.Secret.from_dotenv()])
class ShopLensAnalyzer:

    @modal.enter()
    def load_model(self):
        from ultralytics import YOLO
        from transformers import CLIPModel, CLIPProcessor
        import torch
        self.yolo = YOLO("yolo11n.pt")
        self.clip_model = CLIPModel.from_pretrained("patrickjohncyh/fashion-clip")
        self.clip_processor = CLIPProcessor.from_pretrained("patrickjohncyh/fashion-clip")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.clip_model.to(self.device)
        self.clip_model.eval()
        print(f"[ShopLens] Models loaded on {self.device}")

    def classify_garment(self, pil_image):
        import torch
        inputs = self.clip_processor(
            text=GARMENT_LABELS, images=pil_image,
            return_tensors="pt", padding=True
        ).to(self.device)
        with torch.no_grad():
            probs = self.clip_model(**inputs).logits_per_image.softmax(dim=1)[0]
        scores = [(float(probs[i]), GARMENT_LABELS[i]) for i in range(len(GARMENT_LABELS))]
        scores.sort(reverse=True)
        top_score, top_label = scores[0]
        second_label = scores[1][1]
        print(f"[ShopLens] Garment: {top_label} ({top_score:.2f}), runner-up: {second_label} ({scores[1][0]:.2f})")
        # If confidence is low, return both labels so we run dual queries
        if top_score < 0.55:
            return top_label, second_label
        return top_label, None

    def dominant_color(self, pil_image):
        import numpy as np
        pixels = np.array(pil_image.resize((50, 50))).reshape(-1, 3).astype(float)
        mean = pixels.mean(axis=0)
        color = min(
            COLOR_PALETTE,
            key=lambda c: sum((mean[i] - COLOR_PALETTE[c][i]) ** 2 for i in range(3))
        )
        print(f"[ShopLens] Color: {color}")
        return color

    def detect_pattern(self, pil_image):
        """Zero-shot pattern/texture classification using the same FashionCLIP
        model. Adds crucial visual detail to the text query — 'brown checked shirt'
        finds plaid shirts; 'brown shirt' finds any brown shirt."""
        import torch
        # "solid" is the baseline/default — not added to query.
        # All others are distinct searchable pattern words.
        pattern_labels = [
            "checked", "plaid", "striped", "solid",
            "printed", "floral", "geometric", "embroidered",
        ]
        inputs = self.clip_processor(
            text=pattern_labels, images=pil_image,
            return_tensors="pt", padding=True
        ).to(self.device)
        with torch.no_grad():
            probs = self.clip_model(**inputs).logits_per_image.softmax(dim=1)[0]
        scores = [(float(probs[i]), pattern_labels[i]) for i in range(len(pattern_labels))]
        scores.sort(reverse=True)
        top_score, top_pattern = scores[0]
        print(f"[ShopLens] Pattern: {top_pattern} ({top_score:.2f})")
        # Merge "plaid" → "checked" (same thing in search terms, "checked" is
        # far more common in Indian e-commerce search vocabulary)
        if top_pattern == "plaid":
            top_pattern = "checked"
        # Only include pattern in query if it's meaningfully non-solid
        return "" if top_pattern == "solid" else top_pattern

    def upload_image(self, image_bytes):
        image_url = None
        log = []

        try:
            r = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": ("image.jpg", image_bytes, "image/jpeg")},
                timeout=30,
            )
            log.append(f"catbox={r.status_code}")
            if r.status_code == 200 and r.text.strip().startswith("https://"):
                image_url = r.text.strip()
        except Exception as e:
            log.append(f"catbox=err:{str(e)[:40]}")

        if not image_url:
            try:
                r = requests.post(
                    "https://tmpfiles.org/api/v1/upload",
                    files={"file": ("image.jpg", image_bytes, "image/jpeg")},
                    timeout=30,
                )
                log.append(f"tmpfiles={r.status_code}")
                if r.status_code == 200:
                    raw_url = r.json().get("data", {}).get("url", "")
                    if raw_url:
                        image_url = raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
            except Exception as e:
                log.append(f"tmpfiles=err:{str(e)[:40]}")

        if not image_url:
            try:
                r = requests.post(
                    "https://uguu.se/upload.php",
                    files={"files[]": ("image.jpg", image_bytes, "image/jpeg")},
                    timeout=30,
                )
                log.append(f"uguu={r.status_code}")
                if r.status_code == 200:
                    files = r.json().get("files", [])
                    if files and files[0].get("url"):
                        image_url = files[0]["url"]
            except Exception as e:
                log.append(f"uguu=err:{str(e)[:40]}")

        if not image_url:
            try:
                r = requests.post(
                    "https://0x0.st",
                    files={"file": ("image.jpg", image_bytes, "image/jpeg")},
                    timeout=30,
                )
                log.append(f"0x0={r.status_code}")
                if r.status_code == 200:
                    image_url = r.text.strip()
            except Exception as e:
                log.append(f"0x0=err:{str(e)[:40]}")

        print(f"[ShopLens] Upload log: {log} → {'OK' if image_url else 'FAILED'}")
        return image_url

    def run_parallel_search(self, query, image_url, serpapi_key, alt_query=None):
        from concurrent.futures import ThreadPoolExecutor
        from urllib.parse import quote_plus

        def shopping_search(q):
            """Google Shopping — returns thumbnail + price, but link is a google.com
            catalog page. We replace those with real merchant search URLs derived
            from the `source` field so buyers land on an actual store."""
            try:
                r = requests.get(
                    "https://serpapi.com/search",
                    params={
                        "engine": "google_shopping",
                        "q": q,
                        "gl": "in",
                        "hl": "en",
                        "num": 20,
                        "api_key": serpapi_key,
                    },
                    timeout=30,
                )
                raw = r.json().get("shopping_results", [])
                results = []
                q_enc = quote_plus(q)
                for p in raw:
                    link = p.get("link", "") or p.get("product_link", "")
                    source_name = (p.get("source") or "").lower().strip()

                    # If link is a google.com catalog page, replace with merchant search URL
                    if not link or "google.com" in link:
                        link = ""
                        for key, url_tpl in MERCHANT_SEARCH_URLS.items():
                            if key in source_name:
                                link = url_tpl.format(q_enc)
                                break

                    if link:
                        p["link"] = link
                        results.append({"_src": "shopping", **p})

                print(f"[ShopLens] Shopping '{q}': {len(results)} results")
                sample = [p.get("link", "")[:70] for p in results[:3]]
                print(f"[ShopLens] Shopping URL sample: {sample}")
                return results
            except Exception as e:
                print(f"[ShopLens] Shopping error ({q}): {e}")
                return []

        def organic_search(q):
            """Google organic results — always returns real merchant product URLs,
            but no thumbnail or price. Used to supplement shopping results."""
            try:
                r = requests.get(
                    "https://serpapi.com/search",
                    params={
                        "engine": "google",
                        "q": f"buy {q} online",
                        "gl": "in",
                        "hl": "en",
                        "num": 10,
                        "api_key": serpapi_key,
                    },
                    timeout=30,
                )
                results = r.json().get("organic_results", [])
                print(f"[ShopLens] Organic '{q}': {len(results)} results")
                return [{"_src": "organic", **p} for p in results]
            except Exception as e:
                print(f"[ShopLens] Organic error ({q}): {e}")
                return []

        def google_lens():
            if not image_url:
                print("[ShopLens] Lens skipped — upload failed")
                return []
            try:
                r = requests.get(
                    "https://serpapi.com/search",
                    params={
                        "engine": "google_lens",
                        "url": image_url,
                        "gl": "in",
                        "hl": "en",
                        "api_key": serpapi_key,
                    },
                    timeout=30,
                )
                data = r.json()
                shopping = [{"_src": "lens_shopping", **p} for p in data.get("shopping_results", [])]
                visual = [{"_src": "lens_visual", **p} for p in data.get("visual_matches", [])]
                print(f"[ShopLens] Google Lens: {len(shopping)} shopping, {len(visual)} visual")
                return shopping + visual
            except Exception as e:
                print(f"[ShopLens] Google Lens error: {e}")
                return []

        tasks = [
            (shopping_search, query),
            (organic_search, query),
            (google_lens, None),
        ]
        if alt_query:
            tasks.append((shopping_search, alt_query))

        futures = []
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            for fn, arg in tasks:
                futures.append(executor.submit(fn) if arg is None else executor.submit(fn, arg))
            results = []
            for f in futures:
                results.extend(f.result())
        return results

    def merge_and_score(self, all_results):
        by_src = {}
        for p in all_results:
            s = p.get("_src", "?")
            by_src[s] = by_src.get(s, 0) + 1
        print(f"[ShopLens] Raw by source: {by_src}")

        # Normalise link field — SerpApi Shopping sometimes uses product_link, not link
        for p in all_results:
            if not p.get("link") and p.get("product_link"):
                p["link"] = p["product_link"]

        no_link = sum(1 for p in all_results if not p.get("link"))
        print(f"[ShopLens] Results with no link after normalisation: {no_link}")
        sample_shopping = [p.get("link", "")[:80] for p in all_results if p.get("_src") == "shopping" and p.get("link")][:5]
        print(f"[ShopLens] Shopping URL samples: {sample_shopping}")

        # Lens results are based on the actual image and capture visual details
        # (pattern, texture, cut) that text queries cannot express.
        # Text shopping (score 1) is a fallback only — Lens always wins.
        # lens_visual from INDIAN_BOOST = 4+3 = 7; shopping from INDIAN_BOOST = 1+3 = 4
        SRC_BOOST = {"lens_shopping": 5, "lens_visual": 4, "shopping": 1, "organic": 0}
        blocks = {"blocked_domain": 0, "url_pattern": 0, "lens_visual_not_shopping": 0, "foreign_currency": 0}
        scored = []
        for p in all_results:
            link = p.get("link", "")
            if not link:
                continue

            # Hard block: known bad domains (social, Western e-commerce, news)
            if any(d in link for d in BLOCKED_DOMAINS):
                blocks["blocked_domain"] += 1
                continue

            # Hard block: non-shopping URL patterns (articles, blogs, editorial)
            link_lower = link.lower()
            if any(pat in link_lower for pat in NON_SHOPPING_PATTERNS):
                blocks["url_pattern"] += 1
                continue

            # Lens visual_matches can return celebrity/news pages when Lens
            # recognises the person in the frame. Block those via BLOCKED_DOMAINS
            # (now includes all major Indian entertainment/news sites).
            # Allow any domain NOT in the blocklist — smaller Indian retailers
            # and international sites with Indian stores are both acceptable.
            if p.get("_src") == "lens_visual":
                # Extra guard: URL must look like a product page, not a homepage
                product_signals = ["/product", "/p/", "/dp/", "/item", "/buy",
                                   "-shirt", "-kurta", "-dress", "-jacket",
                                   "-top", "-pant", "-saree", "-kurti", "-suit"]
                is_from_indian_store = any(d in link for d in INDIAN_BOOST)
                has_product_url = any(s in link.lower() for s in product_signals)
                if not is_from_indian_store and not has_product_url:
                    blocks["lens_visual_not_shopping"] += 1
                    continue

            price_str = str(p.get("price", "") or "")

            # Hard block: foreign currency price
            if any(c in price_str for c in FOREIGN_CURRENCY_MARKERS):
                blocks["foreign_currency"] += 1
                continue

            score = SRC_BOOST.get(p.get("_src", ""), 0)

            # Known Indian domain boost
            for domain, boost in INDIAN_BOOST.items():
                if domain in link:
                    score += boost
                    break

            # INR price is the strongest positive signal
            if any(c in price_str for c in INR_MARKERS):
                score += 2
            elif price_str:
                score += 1  # has price but no currency symbol — small boost

            scored.append((score, p))

        print(f"[ShopLens] Filter blocks: {blocks}, passing score: {len(scored)}")
        if scored:
            print(f"[ShopLens] Sample passing URLs: {[p.get('link','')[:60] for _,p in scored[:3]]}")

        scored.sort(key=lambda x: -x[0])

        seen_urls, domain_counts, final = set(), {}, []
        for score, p in scored:
            link = p.get("link", "")
            if link in seen_urls:
                continue
            seen_urls.add(link)
            domain = next((d for d in INDIAN_BOOST if d in link), "other")
            if domain_counts.get(domain, 0) >= 3:
                continue
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            final.append(p)
            if len(final) == 20:
                break

        return final

    @modal.fastapi_endpoint(method="POST")
    def analyze(self, item: dict):
        try:
            import numpy as np

            image_b64 = item.get("image_b64", "")
            if not image_b64:
                return {"products": [], "error": "no image provided"}

            img_bytes = base64.b64decode(image_b64)
            pil_image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img_array = np.array(pil_image)

            # YOLO person detection — crop to largest bounding box
            results = self.yolo(img_array, conf=0.55, classes=[0], verbose=False)
            cropped = img_array
            if results and len(results[0].boxes) > 0:
                boxes = results[0].boxes
                areas = [
                    (b.xyxy[0][2] - b.xyxy[0][0]) * (b.xyxy[0][3] - b.xyxy[0][1])
                    for b in boxes
                ]
                lb = boxes[areas.index(max(areas))]
                x1, y1, x2, y2 = lb.xyxy[0].cpu().numpy().astype(int)
                h, w = img_array.shape[:2]
                cropped = img_array[max(0,y1):min(h,y2), max(0,x1):min(w,x2)]
                print(f"[ShopLens] Person crop: {x2-x1}x{y2-y1}px")

            cropped_pil = Image.fromarray(cropped)

            # Path A inputs: FashionCLIP → garment label + color → search query
            garment, alt_garment = self.classify_garment(cropped_pil)
            color = self.dominant_color(cropped_pil)

            # Append gender for unambiguously gendered garments and suppress
            # alt-query to prevent cross-gender results (e.g. kurta alt for a shirt)
            if garment in MENS_GARMENTS:
                gender_suffix = " men"
                alt_garment = None
            elif garment in WOMENS_GARMENTS:
                gender_suffix = " women"
                alt_garment = None
            else:
                gender_suffix = ""  # kurta/ethnic wear is gender-neutral in India

            query = f"{color} {garment}{gender_suffix}"
            alt_query = f"{color} {alt_garment}{gender_suffix}" if alt_garment else None
            print(f"[ShopLens] Query: '{query}'" + (f" + alt: '{alt_query}'" if alt_query else ""))

            # Path B inputs: encode crop to JPEG → upload for Lens URL
            buf = io.BytesIO()
            cropped_pil.save(buf, format="JPEG", quality=85)
            image_url = self.upload_image(buf.getvalue())

            # Run both paths in parallel
            serpapi_key = os.environ["SERPAPI_KEY"]
            all_results = self.run_parallel_search(query, image_url, serpapi_key, alt_query)
            print(f"[ShopLens] Total raw results: {len(all_results)}")

            top_products = self.merge_and_score(all_results)

            formatted = []
            for p in top_products:
                title = p.get("title", "")
                link = p.get("link", "")
                # Must be a non-empty https URL — drop anything malformed or empty
                if not title or not link or not link.startswith("http"):
                    continue
                if len(title) > 60:
                    title = title[:57] + "..."

                # Price: Shopping results have explicit price field;
                # Organic results may embed it in rich_snippet or snippet text
                price = p.get("price", "")
                if isinstance(price, dict):
                    price = price.get("value") or str(p.get("extracted_price", "")) or ""
                if not price:
                    rich = p.get("rich_snippet", {})
                    exts = (rich.get("top") or rich.get("bottom") or {}).get("extensions", [])
                    for ext in exts:
                        ext_str = str(ext)
                        if any(c in ext_str for c in INR_MARKERS):
                            price = ext_str
                            break
                if not price:
                    import re
                    m = re.search(r'[₹][\s\d,]+', p.get("snippet", ""))
                    if m:
                        price = m.group(0).strip()

                # Source: use explicit field, then displayed_link, then domain from URL
                source = p.get("source", "")
                if not source:
                    dl = p.get("displayed_link", "")
                    source = dl.replace("www.", "").split("/")[0] if dl else ""
                if not source:
                    from urllib.parse import urlparse
                    source = urlparse(link).netloc.replace("www.", "")

                formatted.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "price": str(price) if price else "",
                    "thumbnail": p.get("thumbnail", ""),
                })

            print(f"[ShopLens] Returning {len(formatted)} products, garment={garment}")
            return {"products": formatted, "garment_label": garment}

        except Exception as e:
            import traceback
            print(f"[ShopLens] Exception:\n{traceback.format_exc()}")
            return {"products": [], "error": str(e)[:200]}

    @modal.fastapi_endpoint(method="GET")
    def health(self):
        return {"status": "ok", "service": "shoplens-backend"}

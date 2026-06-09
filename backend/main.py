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

BLOCKED_DOMAINS = {
    # Social media
    "reddit.com", "instagram.com", "facebook.com", "twitter.com",
    "x.com", "tiktok.com", "pinterest.com", "youtube.com",
    "snapchat.com", "tumblr.com", "linkedin.com",
    # Western-only e-commerce
    "amazon.com", "amazon.ca", "amazon.co.uk", "amazon.de",
    "amazon.fr", "amazon.co.jp", "amazon.com.au",
    "etsy.com", "ebay.com", "ebay.co.uk",
    "walmart.com", "target.com", "nordstrom.com", "macys.com",
    "asos.com", "boohoo.com", "shein.com",
    # Google catalog pages
    "google.com",
    # News / editorial
    "wikipedia.org", "vogue.com", "harpersbazaar.com",
    "elle.com", "cosmopolitan.com", "indiatoday.in",
    "hindustantimes.com", "ndtv.com", "timesofindia.com",
    "news18.com", "firstpost.com", "thehindu.com", "indianexpress.com",
    # Indian celebrity/entertainment gossip — Lens returns these for known faces
    "mensxp.com", "scoopwhoop.com", "storypick.com", "pinkvilla.com",
    "filmfare.com", "koimoi.com", "bollywoodlife.com", "spotboye.com",
    "bollywoodshaadis.com", "desimartini.com", "odishatv.in",
    "scroll.in", "thequint.com", "mid-day.com", "dnaindia.com",
}

NON_SHOPPING_PATTERNS = [
    "/article/", "/articles/", "/news/", "/blog/", "/blogs/",
    "/editorial/", "/magazine/", "/watch?", "/post/",
    "/story/", "/stories/", "/review/", "/reviews/",
]

INR_MARKERS = {"₹", "Rs.", "Rs ", "INR"}
FOREIGN_CURRENCY_MARKERS = {
    "US$", "USD", "CA$", "CAD", "AU$", "AUD",
    "£", "GBP", "€", "EUR", "US $", "C$", "$",
}

GARMENT_LABELS = [
    "kurta", "salwar kameez", "saree", "lehenga", "anarkali",
    "dress", "jeans", "trousers", "shirt", "t-shirt",
    "jacket", "blazer", "skirt", "shorts", "ethnic wear",
]
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
        """FashionCLIP — used only for the garment label chip in the UI.
        Does NOT drive search queries. Google Lens handles matching."""
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
        print(f"[ShopLens] Garment label: {top_label} ({top_score:.2f})")
        return top_label

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

    def google_lens_search(self, image_url, serpapi_key):
        """Visual search only — no text queries. Returns shopping_results
        (product cards with prices) and visual_matches (visually similar pages)."""
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
            print(f"[ShopLens] Lens: {len(shopping)} shopping, {len(visual)} visual")
            return shopping + visual
        except Exception as e:
            print(f"[ShopLens] Lens error: {e}")
            return []

    def merge_and_score(self, all_results):
        # lens_shopping: product cards Lens found for the exact image (best)
        # lens_visual: pages with visually similar images, filtered to Indian stores
        SRC_BOOST = {"lens_shopping": 3, "lens_visual": 1}

        blocks = {"blocked_domain": 0, "url_pattern": 0,
                  "lens_visual_not_shopping": 0, "foreign_currency": 0}
        scored = []

        for p in all_results:
            link = p.get("link", "")
            if not link:
                continue

            if any(d in link for d in BLOCKED_DOMAINS):
                blocks["blocked_domain"] += 1
                continue

            link_lower = link.lower()
            if any(pat in link_lower for pat in NON_SHOPPING_PATTERNS):
                blocks["url_pattern"] += 1
                continue

            # lens_visual can return celebrity/news pages when Lens recognises
            # a person in the frame. Only keep results from known Indian stores.
            if p.get("_src") == "lens_visual":
                if not any(d in link for d in INDIAN_BOOST):
                    blocks["lens_visual_not_shopping"] += 1
                    continue

            price_str = str(p.get("price", "") or "")
            if any(c in price_str for c in FOREIGN_CURRENCY_MARKERS):
                blocks["foreign_currency"] += 1
                continue

            score = SRC_BOOST.get(p.get("_src", ""), 0)
            for domain, boost in INDIAN_BOOST.items():
                if domain in link:
                    score += boost
                    break
            if any(c in price_str for c in INR_MARKERS):
                score += 2
            elif price_str:
                score += 1

            scored.append((score, p))

        print(f"[ShopLens] Blocks: {blocks}, passing: {len(scored)}")
        scored.sort(key=lambda x: -x[0])

        seen_urls, domain_counts, final = set(), {}, []
        for score, p in scored:
            link = p.get("link", "")
            if link in seen_urls:
                continue
            seen_urls.add(link)
            domain = next((d for d in INDIAN_BOOST if d in link), "other")
            if domain_counts.get(domain, 0) >= 4:
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
                cropped = img_array[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                print(f"[ShopLens] Person crop: {x2-x1}x{y2-y1}px")

            cropped_pil = Image.fromarray(cropped)

            # FashionCLIP → garment label for the UI chip only (not for search)
            garment = self.classify_garment(cropped_pil)

            # Upload crop and run Lens visual search
            buf = io.BytesIO()
            cropped_pil.save(buf, format="JPEG", quality=85)
            serpapi_key = os.environ["SERPAPI_KEY"]
            image_url = self.upload_image(buf.getvalue())
            all_results = self.google_lens_search(image_url, serpapi_key)
            print(f"[ShopLens] Total raw results: {len(all_results)}")

            top_products = self.merge_and_score(all_results)

            formatted = []
            for p in top_products:
                title = p.get("title", "")
                link = p.get("link", "")
                if not title or not link or not link.startswith("http"):
                    continue
                if len(title) > 60:
                    title = title[:57] + "..."
                price = p.get("price", "")
                if isinstance(price, dict):
                    price = price.get("value") or str(price.get("extracted_price", "")) or ""
                formatted.append({
                    "title": title,
                    "link": link,
                    "source": p.get("source", ""),
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

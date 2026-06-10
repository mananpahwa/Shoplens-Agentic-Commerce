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
        "ultralytics", "pillow", "requests",
        "fastapi[standard]", "python-multipart", "transformers",
    )
    .run_commands(
        "python -c 'from ultralytics import YOLO; YOLO(\"yolo11n.pt\")'",
        "python -c 'from transformers import CLIPModel, CLIPProcessor; "
        "CLIPModel.from_pretrained(\"patrickjohncyh/fashion-clip\"); "
        "CLIPProcessor.from_pretrained(\"patrickjohncyh/fashion-clip\")'"
    )
)

GARMENT_LABELS = [
    "t-shirt", "shirt", "kurta", "dress", "jeans",
    "trousers", "jacket", "blazer", "saree", "lehenga",
    "salwar kameez", "skirt", "shorts",
]

MENS_GARMENTS  = {"shirt", "t-shirt", "trousers", "jeans", "blazer", "shorts", "jacket"}
WOMENS_GARMENTS = {"dress", "saree", "lehenga", "salwar kameez", "skirt"}

COLOR_PALETTE = {
    "red": [210,50,50], "pink": [230,100,150], "orange": [230,130,50],
    "yellow": [220,200,60], "green": [60,150,60], "blue": [50,100,200],
    "navy": [30,50,120], "purple": [130,60,190], "white": [230,230,230],
    "black": [30,30,30], "grey": [128,128,128], "beige": [210,190,150],
    "brown": [130,80,50], "maroon": [128,0,50],
}

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


@app.cls(image=image, gpu="T4", secrets=[modal.Secret.from_dotenv()])
class ShopLensAnalyzer:

    @modal.enter()
    def load_models(self):
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

    def crop_person(self, pil_image):
        """YOLO person detection — return largest-person crop, or full image."""
        import numpy as np
        arr = np.array(pil_image)
        results = self.yolo(arr, conf=0.5, classes=[0], verbose=False)
        if results and len(results[0].boxes) > 0:
            boxes = results[0].boxes
            areas = [
                (b.xyxy[0][2] - b.xyxy[0][0]) * (b.xyxy[0][3] - b.xyxy[0][1])
                for b in boxes
            ]
            lb = boxes[areas.index(max(areas))]
            x1, y1, x2, y2 = lb.xyxy[0].cpu().numpy().astype(int)
            h, w = arr.shape[:2]
            crop = arr[max(0,y1):min(h,y2), max(0,x1):min(w,x2)]
            print(f"[ShopLens] Person crop: {x2-x1}x{y2-y1}px")
            return Image.fromarray(crop)
        print("[ShopLens] No person detected, using full frame")
        return pil_image

    def classify_garment(self, pil_image):
        import torch
        inputs = self.clip_processor(
            text=GARMENT_LABELS, images=pil_image,
            return_tensors="pt", padding=True
        ).to(self.device)
        with torch.no_grad():
            probs = self.clip_model(**inputs).logits_per_image.softmax(dim=1)[0]
        scores = sorted(
            [(float(probs[i]), GARMENT_LABELS[i]) for i in range(len(GARMENT_LABELS))],
            reverse=True
        )
        top_score, top_label = scores[0]
        print(f"[ShopLens] Garment: {top_label} ({top_score:.2f}), runner-up: {scores[1][1]} ({scores[1][0]:.2f})")
        return top_label

    def classify_gender(self, pil_image):
        import torch
        labels = ["a man wearing clothes", "a woman wearing clothes"]
        inputs = self.clip_processor(
            text=labels, images=pil_image,
            return_tensors="pt", padding=True
        ).to(self.device)
        with torch.no_grad():
            probs = self.clip_model(**inputs).logits_per_image.softmax(dim=1)[0]
        gender = "men" if float(probs[0]) >= float(probs[1]) else "women"
        print(f"[ShopLens] Gender: {gender} (man={float(probs[0]):.2f}, woman={float(probs[1]):.2f})")
        return gender

    def dominant_color(self, pil_image):
        import numpy as np
        pixels = np.array(pil_image.resize((50,50))).reshape(-1,3).astype(float)
        mean = pixels.mean(axis=0)
        return min(COLOR_PALETTE, key=lambda c: sum((mean[i]-COLOR_PALETTE[c][i])**2 for i in range(3)))

    def upload_image(self, image_bytes):
        for fn in [self._catbox, self._tmpfiles, self._uguu, self._0x0]:
            try:
                url = fn(image_bytes)
                if url:
                    print(f"[ShopLens] Uploaded: {url}")
                    return url
            except Exception as e:
                print(f"[ShopLens] Upload attempt failed: {e}")
        print("[ShopLens] All uploads failed")
        return None

    def _catbox(self, b):
        r = requests.post("https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": ("image.jpg", b, "image/jpeg")}, timeout=30)
        u = r.text.strip()
        return u if r.status_code == 200 and u.startswith("https://") else None

    def _tmpfiles(self, b):
        r = requests.post("https://tmpfiles.org/api/v1/upload",
            files={"file": ("image.jpg", b, "image/jpeg")}, timeout=30)
        if r.status_code == 200:
            raw = r.json().get("data", {}).get("url", "")
            return raw.replace("tmpfiles.org/", "tmpfiles.org/dl/") if raw else None
        return None

    def _uguu(self, b):
        r = requests.post("https://uguu.se/upload.php",
            files={"files[]": ("image.jpg", b, "image/jpeg")}, timeout=30)
        if r.status_code == 200:
            files = r.json().get("files", [])
            return files[0]["url"] if files else None
        return None

    def _0x0(self, b):
        r = requests.post("https://0x0.st",
            files={"file": ("image.jpg", b, "image/jpeg")}, timeout=30)
        u = r.text.strip()
        return u if r.status_code == 200 and u.startswith("http") else None

    def is_indian(self, url):
        return any(d in url for d in INDIAN_DOMAINS)

    def format_product(self, p):
        link = p.get("link") or p.get("product_link", "")
        title = (p.get("title") or "")[:60]
        source = p.get("source", "")
        if not source and link:
            from urllib.parse import urlparse
            source = urlparse(link).netloc.replace("www.", "")
        return {
            "title": title,
            "link": link,
            "price": str(p.get("price", "") or ""),
            "source": source,
            "thumbnail": p.get("thumbnail", ""),
        }

    @modal.fastapi_endpoint(method="POST")
    def analyze(self, item: dict):
        try:
            import numpy as np

            image_b64 = item.get("image_b64", "")
            if not image_b64:
                return {"products": [], "error": "no image provided"}

            img_bytes = base64.b64decode(image_b64)
            pil_full = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            # ── Step 1: Crop to person ──────────────────────────────────────
            # This removes all background text, captions, and overlays so that
            # both Lens and FashionCLIP see only the outfit, not the scene.
            person_crop = self.crop_person(pil_full)

            # ── Step 2: Upload the CROP (not full frame) for Lens ──────────
            buf = io.BytesIO()
            person_crop.save(buf, format="JPEG", quality=85)
            image_url = self.upload_image(buf.getvalue())

            serpapi_key = os.environ["SERPAPI_KEY"]
            products = []
            seen = set()

            # ── Step 3: Google Lens on person crop ─────────────────────────
            if image_url:
                r = requests.get("https://serpapi.com/search", params={
                    "engine": "google_lens",
                    "url": image_url,
                    "gl": "in", "hl": "en",
                    "api_key": serpapi_key,
                }, timeout=30)
                data = r.json()
                lens_shopping = data.get("shopping_results", [])
                visual_matches = data.get("visual_matches", [])
                print(f"[ShopLens] Lens: {len(lens_shopping)} shopping, {len(visual_matches)} visual")

                for p in lens_shopping + visual_matches:
                    link = p.get("link") or p.get("product_link", "")
                    if not link or link in seen or not self.is_indian(link):
                        continue
                    seen.add(link)
                    products.append(self.format_product(p))

                print(f"[ShopLens] After Lens filter: {len(products)} Indian results")

            # ── Step 4: FashionCLIP fallback ───────────────────────────────
            # Lens rarely returns Indian shopping results for person images.
            # FashionCLIP runs on the person crop (no background text) so
            # classification is about the garment only.
            if len(products) < 4:
                garment = self.classify_garment(person_crop)
                gender  = self.classify_gender(person_crop)
                color   = self.dominant_color(person_crop)

                # Suppress gender for garments that are explicitly gendered
                # in the label (saree/lehenga will always be women's, etc.)
                if garment in MENS_GARMENTS or garment in WOMENS_GARMENTS:
                    gender_suffix = ""  # garment name already implies gender
                else:
                    gender_suffix = f" {gender}"

                query = f"{color} {garment}{gender_suffix}"
                print(f"[ShopLens] FashionCLIP fallback query: '{query}'")

                q_enc = quote_plus(query)
                r = requests.get("https://serpapi.com/search", params={
                    "engine": "google_shopping",
                    "q": query,
                    "gl": "in", "hl": "en",
                    "num": 20,
                    "api_key": serpapi_key,
                }, timeout=30)
                shopping_results = r.json().get("shopping_results", [])
                print(f"[ShopLens] Shopping results: {len(shopping_results)}")

                for p in shopping_results:
                    link = p.get("link") or p.get("product_link", "")
                    source_name = (p.get("source") or "").lower().strip()

                    if not link or "google.com" in link:
                        link = ""
                        for key, tpl in MERCHANT_SEARCH_URLS.items():
                            if key in source_name:
                                link = tpl.format(q_enc)
                                break

                    if not link or not link.startswith("http") or link in seen:
                        continue
                    if not self.is_indian(link):
                        continue

                    seen.add(link)
                    products.append(self.format_product(p))

                print(f"[ShopLens] Total after fallback: {len(products)}")

            return {"products": products[:10], "garment_label": ""}

        except Exception as e:
            import traceback
            print(f"[ShopLens] Exception:\n{traceback.format_exc()}")
            return {"products": [], "error": str(e)[:200]}

    @modal.fastapi_endpoint(method="GET")
    def health(self):
        return {"status": "ok", "service": "shoplens-backend"}

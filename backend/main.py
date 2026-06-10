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
    "zara.com", "hm.com", "uniqlo.com", "mango.com",
    "puma.com", "nike.com", "adidas.co.in", "reebok.in",
    "2 gud.com", "shopclues.com",
]


@app.cls(image=image, secrets=[modal.Secret.from_dotenv()])
class ShopLensAnalyzer:

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

        print(f"[ShopLens] Upload log: {log} → {'OK: ' + image_url if image_url else 'FAILED'}")
        return image_url

    def is_indian_domain(self, url):
        return any(d in url for d in INDIAN_DOMAINS)

    @modal.fastapi_endpoint(method="POST")
    def analyze(self, item: dict):
        try:
            image_b64 = item.get("image_b64", "")
            if not image_b64:
                return {"products": [], "error": "no image provided"}

            img_bytes = base64.b64decode(image_b64)

            # Resize to reasonable dimensions before uploading
            pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            pil.thumbnail((1280, 1280))
            buf = io.BytesIO()
            pil.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()

            image_url = self.upload_image(img_bytes)
            if not image_url:
                return {"products": [], "error": "image upload failed"}

            serpapi_key = os.environ["SERPAPI_KEY"]
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

            shopping = data.get("shopping_results", [])
            visual = data.get("visual_matches", [])
            print(f"[ShopLens] Lens raw: {len(shopping)} shopping, {len(visual)} visual")

            products = []
            seen = set()

            # Shopping results first — always prefer these (have price + thumbnail)
            for p in shopping:
                link = p.get("link") or p.get("product_link", "")
                if not link or not link.startswith("http"):
                    continue
                if link in seen:
                    continue
                if not self.is_indian_domain(link):
                    continue
                seen.add(link)
                title = (p.get("title") or "")[:60]
                products.append({
                    "title": title,
                    "link": link,
                    "price": str(p.get("price", "") or ""),
                    "source": p.get("source", ""),
                    "thumbnail": p.get("thumbnail", ""),
                })

            # Visual matches — filter to Indian shopping domains only
            for p in visual:
                link = p.get("link", "")
                if not link or not link.startswith("http"):
                    continue
                if link in seen:
                    continue
                if not self.is_indian_domain(link):
                    continue
                seen.add(link)
                title = (p.get("title") or "")[:60]
                products.append({
                    "title": title,
                    "link": link,
                    "price": str(p.get("price", "") or ""),
                    "source": p.get("source", ""),
                    "thumbnail": p.get("thumbnail", ""),
                })

            print(f"[ShopLens] Returning {len(products)} Indian products")
            return {"products": products}

        except Exception as e:
            import traceback
            print(f"[ShopLens] Exception:\n{traceback.format_exc()}")
            return {"products": [], "error": str(e)[:200]}

    @modal.fastapi_endpoint(method="GET")
    def health(self):
        return {"status": "ok", "service": "shoplens-backend"}

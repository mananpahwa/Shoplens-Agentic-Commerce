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
    .pip_install("ultralytics", "pillow", "requests", "fastapi[standard]", "python-multipart")
    # Pre-download YOLO model into the image at build time.
    # Without this, every cold start downloads ~6MB from internet, adding 30-60s.
    .run_commands("python -c 'from ultralytics import YOLO; YOLO(\"yolo11n.pt\")'")
)


@app.cls(image=image, gpu="T4", secrets=[modal.Secret.from_dotenv()])
class ShopLensAnalyzer:

    @modal.enter()
    def load_model(self):
        # Runs once when the container starts — model stays in memory across requests.
        from ultralytics import YOLO
        self.model = YOLO("yolo11n.pt")

    @modal.fastapi_endpoint(method="POST")
    def analyze(self, item: dict):
        try:
            import numpy as np

            image_b64 = item.get("image_b64", "")
            if not image_b64:
                return {"products": [], "error": "no image provided"}

            # Decode base64 → PIL Image
            img_bytes = base64.b64decode(image_b64)
            pil_image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img_array = np.array(pil_image)

            # YOLO person detection — model already loaded in memory
            results = self.model(img_array, conf=0.55, classes=[0], verbose=False)

            cropped = img_array
            if results and len(results[0].boxes) > 0:
                boxes = results[0].boxes
                areas = []
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                    areas.append((x2 - x1) * (y2 - y1))
                largest_idx = areas.index(max(areas))
                largest_box = boxes[largest_idx]
                x1, y1, x2, y2 = largest_box.xyxy[0].cpu().numpy().astype(int)
                h, w = img_array.shape[:2]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                cropped = img_array[y1:y2, x1:x2]

            # Convert to JPEG bytes
            cropped_pil = Image.fromarray(cropped)
            buf = io.BytesIO()
            cropped_pil.save(buf, format="JPEG", quality=85)
            cropped_bytes = buf.getvalue()

            # Upload image to a public host so SerpApi can fetch it.
            # Try multiple hosts in order — some block cloud datacenter IPs.
            image_url = None
            upload_log = []

            # Host 1: catbox.moe
            try:
                r = requests.post(
                    "https://catbox.moe/user/api.php",
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": ("image.jpg", cropped_bytes, "image/jpeg")},
                    timeout=30,
                )
                upload_log.append(f"catbox={r.status_code}:{r.text.strip()[:80]}")
                if r.status_code == 200 and r.text.strip().startswith("https://"):
                    image_url = r.text.strip()
            except Exception as e:
                upload_log.append(f"catbox=err:{str(e)[:50]}")

            # Host 2: tmpfiles.org
            if not image_url:
                try:
                    r = requests.post(
                        "https://tmpfiles.org/api/v1/upload",
                        files={"file": ("image.jpg", cropped_bytes, "image/jpeg")},
                        timeout=30,
                    )
                    upload_log.append(f"tmpfiles={r.status_code}:{r.text.strip()[:80]}")
                    if r.status_code == 200:
                        data = r.json()
                        raw_url = data.get("data", {}).get("url", "")
                        if raw_url:
                            # tmpfiles page URL → direct download URL
                            image_url = raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                except Exception as e:
                    upload_log.append(f"tmpfiles=err:{str(e)[:50]}")

            # Host 3: uguu.se
            if not image_url:
                try:
                    r = requests.post(
                        "https://uguu.se/upload.php",
                        files={"files[]": ("image.jpg", cropped_bytes, "image/jpeg")},
                        timeout=30,
                    )
                    upload_log.append(f"uguu={r.status_code}:{r.text.strip()[:80]}")
                    if r.status_code == 200:
                        data = r.json()
                        files = data.get("files", [])
                        if files and files[0].get("url"):
                            image_url = files[0]["url"]
                except Exception as e:
                    upload_log.append(f"uguu=err:{str(e)[:50]}")

            # Host 4: 0x0.st
            if not image_url:
                try:
                    r = requests.post(
                        "https://0x0.st",
                        files={"file": ("image.jpg", cropped_bytes, "image/jpeg")},
                        timeout=30,
                    )
                    upload_log.append(f"0x0={r.status_code}:{r.text.strip()[:80]}")
                    if r.status_code == 200:
                        image_url = r.text.strip()
                except Exception as e:
                    upload_log.append(f"0x0=err:{str(e)[:50]}")

            if not image_url:
                return {"products": [], "error": f"upload_failed: {upload_log}"}

            # SerpApi Google Lens
            serpapi_key = os.environ["SERPAPI_KEY"]
            lens_resp = requests.get(
                "https://serpapi.com/search",
                params={
                    "engine": "google_lens",
                    "search_type": "products",
                    "gl": "in",
                    "hl": "en",
                    "url": image_url,
                    "api_key": serpapi_key,
                },
                timeout=30,
            )
            try:
                lens_data = lens_resp.json()
            except Exception:
                return {"products": []}

            # Priority 1: shopping_results — actual product cards with prices
            # Priority 2: visual_matches filtered to known Indian e-commerce domains
            # Never return social media, news, or general web pages.

            SHOPPING_DOMAINS = [
                "flipkart.com", "myntra.com", "amazon.in", "amazon.com",
                "meesho.com", "ajio.com", "nykaa.com", "snapdeal.com",
                "tatacliq.com", "reliancetrends.com", "westside.com",
                "limeroad.com", "koovs.com", "jabong.com", "shopclues.com",
                "shein.in", "zara.com", "hm.com", "uniqlo.com",
                "bewakoof.com", "urbanic.com", "virgio.com",
            ]

            raw_products = lens_data.get("shopping_results") or []

            if not raw_products:
                # Fall back to visual_matches but only keep shopping domain links
                all_matches = lens_data.get("visual_matches") or []
                raw_products = [
                    p for p in all_matches
                    if any(domain in p.get("link", "") for domain in SHOPPING_DOMAINS)
                ]

            formatted = []
            for p in raw_products[:3]:
                title = p.get("title", "")
                if len(title) > 60:
                    title = title[:57] + "..."

                price = p.get("price", "")
                if isinstance(price, dict):
                    price = price.get("value") or price.get("extracted_price") or ""

                link = p.get("link", "")
                if not title or not link:
                    continue

                formatted.append({
                    "title": title,
                    "link": link,
                    "source": p.get("source", ""),
                    "price": str(price) if price else "",
                    "thumbnail": p.get("thumbnail", ""),
                })

            return {"products": formatted}

        except Exception as e:
            return {"products": [], "error": str(e)[:100]}

    @modal.fastapi_endpoint(method="GET")
    def health(self):
        return {"status": "ok", "service": "shoplens-backend"}

#!/usr/bin/env python3
"""Download DSO images for all objects in dso_targets.yaml.

Run: python scripts/download_dso_images.py

Images saved to: assets/dso_images/{name}.jpg  (spaces/slashes → underscores)

For each object:
  1. Try curated image_url from YAML first
  2. Fall back to Aladin hips2fits (built from RA/Dec)
  3. If curated URL fails, retry with Aladin
  4. Validate response is a real image (Content-Type check + Pillow open)
  5. Resize to 400x400 JPEG, save

Already-downloaded images are skipped (idempotent — safe to re-run).
"""

import sys
from pathlib import Path
from io import BytesIO

import yaml
import requests
from PIL import Image

# Add project root to sys.path so backend imports work
sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.app_logic import _get_dso_image_url  # noqa: E402

ASSETS_DIR  = Path(__file__).parent.parent / "assets" / "dso_images"
YAML_PATH   = Path(__file__).parent.parent / "dso_targets.yaml"
TARGET_SIZE = (400, 400)
TIMEOUT     = 30
HEADERS     = {"User-Agent": "AstroPlanner/1.0 (astronomy observation planner)"}


def sanitize_filename(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_") + ".jpg"


def download_image(url: str, dest: Path) -> bool:
    """Download url, validate as image, resize to 400x400, save as JPEG.
    Returns True on success, False on any failure.
    """
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        if resp.status_code != 200:
            return False
        content_type = resp.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            return False
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img = img.resize(TARGET_SIZE, Image.LANCZOS)
        img.save(dest, "JPEG", quality=85)
        return True
    except Exception:
        return False


def main():
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))

    all_objects = [obj for section in data.values() for obj in section]

    ok = skip = fail = 0

    for obj in all_objects:
        name     = obj["name"]
        filename = sanitize_filename(name)
        dest     = ASSETS_DIR / filename

        if dest.exists():
            print(f"  {name}: skip (exists)")
            skip += 1
            continue

        ra       = float(obj.get("ra", 0))
        dec      = float(obj.get("dec", 0))
        obj_type = obj.get("type", "")
        curated  = obj.get("image_url") or None

        # Try curated URL first, then Aladin
        primary_url = _get_dso_image_url(ra, dec, obj_type, curated)
        source = "Wikimedia" if curated else "Aladin"

        if download_image(primary_url, dest):
            print(f"  {name}: OK ({source})")
            ok += 1
        elif curated:
            # Curated URL failed — try Aladin as fallback
            aladin_url = _get_dso_image_url(ra, dec, obj_type, None)
            if download_image(aladin_url, dest):
                print(f"  {name}: OK (Aladin fallback — curated URL failed)")
                ok += 1
            else:
                print(f"  {name}: FAILED")
                fail += 1
        else:
            print(f"  {name}: FAILED")
            fail += 1

    print(f"\nDone: {ok} downloaded, {skip} skipped, {fail} failed")
    print(f"Images saved to: {ASSETS_DIR}")


if __name__ == "__main__":
    main()

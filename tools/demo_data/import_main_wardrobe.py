#!/usr/bin/env python3
"""Bulk-import 40 wardrobe items into the active OutfitDB profile (Tester).

Reads /tmp/wardrobe_spec.json, then for each slot uploads the matching
JPG from profiles/Tester/items/images/{slot}.jpg via POST /items/upload
(multipart form). Forms colors/style_tags as JSON strings as the
endpoint expects.

Run while uvicorn is serving the Tester profile.
"""
import json
import sys
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"
SPEC = Path("/tmp/wardrobe_spec.json")
IMG_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")

def main():
    items = json.loads(SPEC.read_text())

    # Verify Tester is the active profile before we mutate any DB
    p = requests.get(f"{BASE}/profiles", timeout=5).json()
    if p.get("active") != "Tester":
        print(f"ABORT: active profile is {p.get('active')!r}, expected 'Tester'")
        print("Switch via UI/API first; this script will not change the active profile.")
        sys.exit(1)

    # Skip if items already exist (avoid duplicates on re-run)
    existing = requests.get(f"{BASE}/items/", timeout=10).json()
    if existing:
        print(f"WARN: Tester already has {len(existing)} items. Refusing to add duplicates.")
        print("Delete existing items first if you want a clean import.")
        sys.exit(2)

    created = 0
    failed = []
    for it in items:
        slot = it["slot"]
        img_path = IMG_DIR / f"{slot}.jpg"
        if not img_path.exists():
            failed.append((slot, "image missing"))
            continue
        form = {
            "name": it["name"],
            "category": it["category"],
            "colors": json.dumps(it["colors"]),
            "is_multicolor": "true" if len(it["colors"]) > 1 else "false",
            "pattern": it["pattern"],
            "pattern_complexity": "1" if it["pattern"] != "solid" else "0",
            "material": it["material"],
            "thickness": it["thickness"],
            "style_tags": json.dumps(it.get("style_tags", [])),
            "can_wear_alone": "true" if it["can_wear_alone"] else "false",
        }
        # optional cut/layout fields
        for k in ("collar", "top_length", "sleeve", "pants_length", "pants_fit"):
            if k in it:
                form[k] = it[k]

        with img_path.open("rb") as fh:
            files = {"image": (f"{slot}.jpg", fh, "image/jpeg")}
            r = requests.post(f"{BASE}/items/upload", data=form, files=files, timeout=30)
        if r.status_code != 200:
            failed.append((slot, f"HTTP {r.status_code}: {r.text[:200]}"))
            continue
        out = r.json()
        created += 1
        print(f"  slot {slot} → id={out['id']:>3} {out['name']}")

    total = len(items)
    print(f"\nImported {created}/{total} items, {len(failed)} failed")
    for slot, err in failed:
        print(f"  slot {slot}: {err}")

if __name__ == "__main__":
    main()

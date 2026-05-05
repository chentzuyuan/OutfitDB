#!/usr/bin/env python3
"""Bulk-import slots 41-50 (sport/casual variety) into the active Tester profile."""
import json
import sys
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"
SPEC = Path("/tmp/wardrobe_spec_extra.json")
IMG_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")


def main():
    items = json.loads(SPEC.read_text())
    p = requests.get(f"{BASE}/profiles", timeout=5).json()
    if p.get("active") != "Tester":
        print(f"ABORT: active profile is {p.get('active')!r}, expected 'Tester'")
        sys.exit(1)

    existing = requests.get(f"{BASE}/items/", timeout=10).json()
    existing_names = {it["name"] for it in existing}

    created = 0
    skipped = 0
    for it in items:
        if it["name"] in existing_names:
            print(f"  skip {it['name']} (already exists)")
            skipped += 1
            continue
        slot = it["slot"]
        img_path = IMG_DIR / f"{slot}.jpg"
        if not img_path.exists():
            print(f"  slot {slot}: image missing")
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
        for k in ("collar", "top_length", "sleeve", "pants_length", "pants_fit"):
            if k in it:
                form[k] = it[k]
        with img_path.open("rb") as fh:
            files = {"image": (f"{slot}.jpg", fh, "image/jpeg")}
            r = requests.post(f"{BASE}/items/upload", data=form, files=files, timeout=30)
        if r.status_code != 200:
            print(f"  slot {slot}: HTTP {r.status_code}: {r.text[:200]}")
            continue
        out = r.json()
        created += 1
        print(f"  slot {slot} -> id={out['id']} {out['name']}")

    print(f"\nCreated {created}, skipped {skipped}")


if __name__ == "__main__":
    main()

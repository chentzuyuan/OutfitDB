#!/usr/bin/env python3
"""Bulk-import slots 61-63 into Tester profile."""
import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"
SPEC = Path("/tmp/wardrobe_spec_cold_formal.json")
IMG_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")

items = json.loads(SPEC.read_text())
for it in items:
    slot = it["slot"]
    img = IMG_DIR / f"{slot}.jpg"
    form = {
        "name": it["name"], "category": it["category"],
        "colors": json.dumps(it["colors"]), "is_multicolor": "false",
        "pattern": it["pattern"], "pattern_complexity": "0",
        "material": it["material"], "thickness": it["thickness"],
        "style_tags": json.dumps(it.get("style_tags", [])),
        "can_wear_alone": "true",
    }
    for k in ("collar", "top_length", "sleeve", "pants_length", "pants_fit"):
        if k in it: form[k] = it[k]
    with img.open("rb") as fh:
        r = requests.post(f"{BASE}/items/upload", data=form,
                          files={"image": (f"{slot}.jpg", fh, "image/jpeg")},
                          timeout=30)
    out = r.json()
    print(f"  slot {slot} -> id={out.get('id')} {out.get('name')}")

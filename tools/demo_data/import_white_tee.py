#!/usr/bin/env python3
"""Re-add the White Cotton T-Shirt that was previously deleted, now with real DALL-E."""
import json
from pathlib import Path
import requests

BASE = "http://127.0.0.1:8000"
IMG = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images/41.jpg")

form = {
    "name": "White Cotton T-Shirt",
    "category": "top",
    "colors": json.dumps(["white"]),
    "is_multicolor": "false",
    "pattern": "solid",
    "pattern_complexity": "0",
    "material": "cotton",
    "thickness": "thin",
    "style_tags": json.dumps(["casual", "minimal"]),
    "can_wear_alone": "true",
    "sleeve": "short",
    "collar": "crew",
}
with IMG.open("rb") as fh:
    r = requests.post(f"{BASE}/items/upload", data=form,
                      files={"image": ("41.jpg", fh, "image/jpeg")},
                      timeout=30)
out = r.json()
print(f"Created id={out.get('id')} name={out.get('name')}")
print(f"image_path={out.get('image_path')}")

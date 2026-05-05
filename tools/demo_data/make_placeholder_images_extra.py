#!/usr/bin/env python3
"""Generate placeholders for slots 41-50 (sport/casual variety set)."""
import json
import sys
from pathlib import Path

# Reuse helpers from main placeholder script
sys.path.insert(0, "/tmp")
from make_placeholders import COLOR_RGB, draw_garment_silhouette, load_font  # noqa
from PIL import Image, ImageDraw

SPEC = Path("/tmp/wardrobe_spec_extra.json")
OUT_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")


def main():
    items = json.loads(SPEC.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name_font = load_font(54)
    sub_font = load_font(36)

    for it in items:
        slot = it["slot"]
        primary = it["colors"][0]
        rgb = COLOR_RGB.get(primary, (180, 180, 180))
        if primary == "yellow":
            rgb = (235, 200, 60)
        bright = sum(rgb) / 3
        outline = (40, 40, 40) if bright > 130 else (235, 235, 235)
        canvas = Image.new("RGB", (1024, 1024), (250, 250, 250))
        draw = ImageDraw.Draw(canvas)
        draw_garment_silhouette(draw, it["category"], rgb, outline)
        draw.text((512, 80), it["name"], font=name_font, fill=(40, 40, 40), anchor="mm")
        sub = f"{it['category']} - {it['thickness']}"
        draw.text((512, 940), sub, font=sub_font, fill=(110, 110, 110), anchor="mm")
        out = OUT_DIR / f"{slot}.jpg"
        canvas.save(out, "JPEG", quality=92)
        print(f"slot {slot}: {it['name']} -> {out.name}")


if __name__ == "__main__":
    main()

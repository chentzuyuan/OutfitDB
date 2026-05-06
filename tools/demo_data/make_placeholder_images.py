#!/usr/bin/env python3
"""Generate placeholder JPG images for OutfitDB Tester wardrobe slots 22-40.

Each placeholder is a 1024x1024 color-coded card with the item name and
a stylized garment glyph indicating the category. The PIL output mirrors
the on-white DALL-E aesthetic so the wardrobe view stays visually
consistent.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SPEC_PATH = Path("/tmp/wardrobe_spec.json")
OUT_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")

# Map color name → (RGB, label color)
COLOR_RGB = {
    "white":  (240, 240, 240),
    "black":  (30, 30, 30),
    "gray":   (140, 140, 145),
    "navy":   (28, 38, 80),
    "blue":   (66, 110, 200),
    "red":    (140, 40, 60),
    "green":  (90, 110, 60),
    "brown":  (110, 80, 50),
    "beige":  (210, 190, 160),
}

# Slot ranges that still need a placeholder
import sys
if len(sys.argv) > 1 and sys.argv[1] == "--slots":
    SLOTS_TO_DRAW = set(sys.argv[2:])
else:
    SLOTS_TO_DRAW = set(f"{i:02d}" for i in range(22, 41))

# Try to find a system font
FONT_CANDIDATES = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

def load_font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                continue
    return ImageFont.load_default()

def draw_garment_silhouette(draw: ImageDraw.ImageDraw, category: str, fill, outline):
    """Draw a simple silhouette centered on a 1024x1024 canvas."""
    cx, cy = 512, 512
    w, h = 480, 600
    if category == "top":
        # T-shirt-like body
        body = [(cx - 220, cy - 240), (cx + 220, cy + 240)]
        sleeve_l = [(cx - 360, cy - 240), (cx - 220, cy - 60)]
        sleeve_r = [(cx + 220, cy - 240), (cx + 360, cy - 60)]
        draw.rectangle(body, fill=fill, outline=outline, width=4)
        draw.rectangle(sleeve_l, fill=fill, outline=outline, width=4)
        draw.rectangle(sleeve_r, fill=fill, outline=outline, width=4)
    elif category == "bottom":
        leg_l = [(cx - 200, cy - 300), (cx - 20, cy + 300)]
        leg_r = [(cx + 20, cy - 300), (cx + 200, cy + 300)]
        draw.rectangle(leg_l, fill=fill, outline=outline, width=4)
        draw.rectangle(leg_r, fill=fill, outline=outline, width=4)
        # waistband
        draw.rectangle([(cx - 220, cy - 320), (cx + 220, cy - 290)], fill=outline)
    elif category == "shoes":
        body = [(cx - 280, cy + 40), (cx + 280, cy + 220)]
        toe = [(cx + 200, cy - 60), (cx + 280, cy + 40)]
        draw.rectangle(body, fill=fill, outline=outline, width=4)
        draw.polygon([(cx + 200, cy + 40), (cx + 280, cy + 40), (cx + 280, cy - 60)],
                     fill=fill, outline=outline)
    elif category == "accessory":
        draw.rectangle([(cx - 200, cy - 60), (cx + 200, cy + 60)], fill=fill,
                       outline=outline, width=4)
    elif category == "fullbody":
        # combined top + bottom
        draw.rectangle([(cx - 220, cy - 320), (cx + 220, cy - 80)], fill=fill,
                       outline=outline, width=4)
        draw.rectangle([(cx - 200, cy - 80), (cx - 20, cy + 300)], fill=fill,
                       outline=outline, width=4)
        draw.rectangle([(cx + 20, cy - 80), (cx + 200, cy + 300)], fill=fill,
                       outline=outline, width=4)

def main():
    items = json.loads(SPEC_PATH.read_text())
    by_slot = {it["slot"]: it for it in items}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    name_font = load_font(54)
    sub_font = load_font(36)

    for slot in sorted(SLOTS_TO_DRAW):
        item = by_slot[slot]
        primary = item["colors"][0]
        rgb = COLOR_RGB.get(primary, (180, 180, 180))
        # outline color: dark on light, light on dark
        bright = sum(rgb) / 3
        outline = (40, 40, 40) if bright > 130 else (235, 235, 235)
        # background: pure white to match DALL-E aesthetic
        canvas = Image.new("RGB", (1024, 1024), (250, 250, 250))
        draw = ImageDraw.Draw(canvas)
        draw_garment_silhouette(draw, item["category"], rgb, outline)
        # Header line
        draw.text((512, 80), item["name"], font=name_font, fill=(40, 40, 40), anchor="mm")
        # Sub line
        sub = f"{item['category']} - {item['thickness']}"
        draw.text((512, 940), sub, font=sub_font, fill=(110, 110, 110), anchor="mm")
        out = OUT_DIR / f"{slot}.jpg"
        canvas.save(out, "JPEG", quality=92)
        print(f"slot {slot}: {item['name']} → {out.name}")

    total = len(list(OUT_DIR.glob("*.jpg")))
    print(f"\nTotal images now in Tester wardrobe: {total}/40")

if __name__ == "__main__":
    main()

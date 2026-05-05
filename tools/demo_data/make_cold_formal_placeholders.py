#!/usr/bin/env python3
"""Generate Pillow silhouettes for the 3 cold formal items (slots 61-63)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")
FONT_CANDIDATES = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]

def load_font(size):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try: return ImageFont.truetype(p, size=size)
            except Exception: continue
    return ImageFont.load_default()

def draw_overcoat(draw, body_color, accent_color):
    cx = 512
    # Wide trapezoid with knee-length silhouette
    body = [(cx-300, 220), (cx+300, 220), (cx+340, 880), (cx-340, 880)]
    draw.polygon(body, fill=body_color)
    # Sleeves long
    draw.polygon([(cx-300, 220), (cx-410, 280), (cx-380, 800), (cx-280, 760)], fill=body_color)
    draw.polygon([(cx+300, 220), (cx+410, 280), (cx+380, 800), (cx+280, 760)], fill=body_color)
    # Collar / lapels
    draw.polygon([(cx-50, 220), (cx-180, 270), (cx-50, 540), (cx, 480)], fill=accent_color)
    draw.polygon([(cx+50, 220), (cx+180, 270), (cx+50, 540), (cx, 480)], fill=accent_color)
    # Single column of buttons (single-breasted overcoat)
    for y in (480, 580, 680, 780):
        draw.ellipse([(cx-12, y-12), (cx+12, y+12)], fill=accent_color)

def draw_peacoat(draw, body_color, accent_color):
    cx = 512
    body = [(cx-280, 220), (cx+280, 220), (cx+320, 800), (cx-320, 800)]
    draw.polygon(body, fill=body_color)
    draw.polygon([(cx-280, 220), (cx-380, 280), (cx-360, 740), (cx-260, 720)], fill=body_color)
    draw.polygon([(cx+280, 220), (cx+380, 280), (cx+360, 740), (cx+260, 720)], fill=body_color)
    # Notched lapels
    draw.polygon([(cx-40, 220), (cx-160, 270), (cx-40, 540), (cx, 480)], fill=accent_color)
    draw.polygon([(cx+40, 220), (cx+160, 270), (cx+40, 540), (cx, 480)], fill=accent_color)
    # Two columns (double-breasted)
    for y in (490, 590, 690):
        for col in (-50, 50):
            draw.ellipse([(cx+col-14, y-14), (cx+col+14, y+14)], fill=accent_color)

def draw_scarf(draw, color):
    cx, cy = 512, 512
    # Folded scarf — two long rectangles overlapping
    draw.rectangle([(cx-300, cy-180), (cx+300, cy-60)], fill=color, outline=(120,120,120), width=2)
    draw.rectangle([(cx-280, cy-50), (cx+320, cy+80)], fill=color, outline=(120,120,120), width=2)
    draw.rectangle([(cx-260, cy+90), (cx+340, cy+220)], fill=color, outline=(120,120,120), width=2)

def make(slot, name, painter):
    img = Image.new("RGB", (1024, 1024), (235, 235, 235))
    d = ImageDraw.Draw(img)
    painter(d)
    nf = load_font(48)
    d.text((512, 80), name, font=nf, fill=(40,40,40), anchor="mm")
    out = OUT_DIR / f"{slot}.jpg"
    img.save(out, "JPEG", quality=92)
    print(f"slot {slot}: {name} -> {out.name}")

# Slot 61: Black Wool Peacoat — body=very dark, accent=slightly lighter
make("61", "Black Wool Peacoat", lambda d: draw_peacoat(d, (28, 28, 32), (55, 55, 60)))
# Slot 62: Navy Herringbone Overcoat
make("62", "Navy Herringbone Overcoat", lambda d: draw_overcoat(d, (28, 38, 70), (50, 60, 95)))
# Slot 63: Charcoal Cashmere Scarf
make("63", "Charcoal Cashmere Scarf", lambda d: draw_scarf(d, (75, 75, 80)))

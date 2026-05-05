#!/usr/bin/env python3
"""Generate stylized clothing illustrations for the Tuxedo split (jacket + trousers).

Higher fidelity than the basic make_placeholders.py rectangles — draws
actual lapel + collar + button silhouette for the jacket, and matching
trouser shape with waist/leg curves. Output saved as slot 40.jpg
(jacket) and slot 60.jpg (trousers).
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")
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


def draw_tuxedo_jacket(draw: ImageDraw.ImageDraw):
    """Draw a black tuxedo jacket silhouette: shoulders, lapels, button."""
    cx = 512
    body_color = (35, 35, 38)
    satin_color = (60, 60, 65)
    # Shoulders + body trapezoid
    body = [
        (cx - 280, 280),    # left shoulder
        (cx + 280, 280),    # right shoulder
        (cx + 320, 750),    # right hem
        (cx - 320, 750),    # left hem
    ]
    draw.polygon(body, fill=body_color)
    # Sleeves
    draw.polygon([(cx - 280, 280), (cx - 380, 320), (cx - 360, 700), (cx - 260, 680)], fill=body_color)
    draw.polygon([(cx + 280, 280), (cx + 380, 320), (cx + 360, 700), (cx + 260, 680)], fill=body_color)
    # Lapels (satin shine)
    left_lapel = [(cx - 60, 280), (cx - 180, 330), (cx - 60, 600), (cx, 540)]
    right_lapel = [(cx + 60, 280), (cx + 180, 330), (cx + 60, 600), (cx, 540)]
    draw.polygon(left_lapel, fill=satin_color)
    draw.polygon(right_lapel, fill=satin_color)
    # Collar V-cut (white shirt peek)
    draw.polygon([(cx - 30, 280), (cx + 30, 280), (cx, 380)], fill=(245, 245, 245))
    # Button
    draw.ellipse([(cx - 12, 580), (cx + 12, 604)], fill=satin_color)


def draw_tuxedo_trousers(draw: ImageDraw.ImageDraw):
    """Draw a pair of black tuxedo trousers with satin side stripe."""
    cx = 512
    body_color = (35, 35, 38)
    stripe_color = (75, 75, 80)
    # Waistband
    draw.rectangle([(cx - 200, 220), (cx + 200, 270)], fill=body_color)
    # Right leg
    draw.polygon([
        (cx + 20, 260),
        (cx + 200, 260),
        (cx + 220, 820),
        (cx + 60, 820),
    ], fill=body_color)
    # Left leg
    draw.polygon([
        (cx - 20, 260),
        (cx - 200, 260),
        (cx - 220, 820),
        (cx - 60, 820),
    ], fill=body_color)
    # Satin stripes (signature tuxedo detail)
    draw.line([(cx - 200, 270), (cx - 220, 820)], fill=stripe_color, width=8)
    draw.line([(cx + 200, 270), (cx + 220, 820)], fill=stripe_color, width=8)


def make(slot: str, name: str, drawer):
    canvas = Image.new("RGB", (1024, 1024), (235, 235, 235))   # soft gray bg, makes black pop
    draw = ImageDraw.Draw(canvas)
    drawer(draw)
    name_font = load_font(48)
    sub_font = load_font(32)
    draw.text((512, 80), name, font=name_font, fill=(40, 40, 40), anchor="mm")
    out = OUT_DIR / f"{slot}.jpg"
    canvas.save(out, "JPEG", quality=92)
    print(f"slot {slot}: {name} -> {out.name}")


# Reuse slot 40 (deleted) for the jacket; new slot 60 for trousers (avoid clash with 41-50)
make("40", "Black Tuxedo Jacket", draw_tuxedo_jacket)
make("60", "Black Tuxedo Trousers", draw_tuxedo_trousers)

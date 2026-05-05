"""Generate simple PNG icons (192/512/180) under app/static/icons/.
Pure-color background + 'CM' text. Replace with proper artwork later."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def make_icon(size: int, filename: str, bg="#111111", fg="#ffffff", text="CM"):
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    # try to load a system font; fall back to default
    font = None
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in candidates:
        try:
            font = ImageFont.truetype(c, int(size * 0.42))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, fill=fg, font=font)
    out = OUT_DIR / filename
    img.save(out, "PNG")
    print(f"wrote {out}")


if __name__ == "__main__":
    make_icon(192, "icon-192.png")
    make_icon(512, "icon-512.png")
    make_icon(180, "apple-touch-icon-180.png")

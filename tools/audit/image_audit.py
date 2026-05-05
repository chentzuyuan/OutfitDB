#!/usr/bin/env python3
"""Audit Tester wardrobe images for content/name mismatches.

Flags (skip too-white per user request):
  - File size < 50KB → likely Pillow placeholder leftover
  - Duplicate MD5 across items → swapped or accidentally shared
  - Color attribute vs image dominant color mismatch (e.g. item says "navy"
    but image dominant is brown)
"""
import hashlib
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from PIL import Image

DB = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/wardrobe.db")
IMG_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")

# Approximate RGB centers for each color name
COLOR_CENTROIDS = {
    "white":  (240, 240, 240),
    "black":  (25, 25, 25),
    "gray":   (130, 130, 130),
    "navy":   (28, 38, 70),
    "blue":   (60, 110, 200),
    "brown":  (100, 65, 40),
    "beige":  (210, 195, 165),
    "green":  (90, 130, 70),
    "red":    (170, 40, 40),
    "pink":   (230, 170, 180),
    "yellow": (235, 200, 60),
    "orange": (230, 130, 50),
    "purple": (110, 70, 140),
}

def color_distance(rgb_a, rgb_b):
    return ((rgb_a[0]-rgb_b[0])**2 + (rgb_a[1]-rgb_b[1])**2 + (rgb_a[2]-rgb_b[2])**2) ** 0.5

def dominant_non_bg_color(img: Image.Image) -> tuple:
    """Find the most common non-near-white color (the actual garment color)."""
    pixels = list(img.resize((128, 128)).getdata())
    # Filter out near-white (background) and near-black (shadows)
    non_bg = [p for p in pixels if not (p[0] > 220 and p[1] > 220 and p[2] > 220) and not (p[0] < 30 and p[1] < 30 and p[2] < 30)]
    if not non_bg:
        # fall back to all non-white
        non_bg = [p for p in pixels if not (p[0] > 220 and p[1] > 220 and p[2] > 220)]
    if not non_bg:
        return None
    # Average those
    n = len(non_bg)
    return (sum(p[0] for p in non_bg)//n, sum(p[1] for p in non_bg)//n, sum(p[2] for p in non_bg)//n)


def main():
    conn = sqlite3.connect(DB)
    rows = conn.execute("""
        SELECT id, name, image_path, colors
        FROM items
        WHERE image_path IS NOT NULL
        ORDER BY id
    """).fetchall()
    conn.close()

    issues = defaultdict(list)
    md5_to_items = defaultdict(list)
    file_info = []

    for item_id, name, img_path, colors_json in rows:
        # image_path looks like "/images/63_1777622919.jpg"
        fname = img_path.lstrip("/").replace("images/", "")
        full = IMG_DIR / fname
        if not full.exists():
            issues["missing_file"].append((item_id, name, fname))
            continue

        size = full.stat().st_size
        with full.open("rb") as fh:
            md5 = hashlib.md5(fh.read()).hexdigest()
        md5_to_items[md5].append((item_id, name))

        try:
            img = Image.open(full).convert("RGB")
        except Exception as e:
            issues["unreadable"].append((item_id, name, str(e)))
            continue

        pixels = list(img.getdata())
        n = len(pixels)
        avg_r = sum(p[0] for p in pixels) / n
        avg_g = sum(p[1] for p in pixels) / n
        avg_b = sum(p[2] for p in pixels) / n

        # Skip too-white per user request
        if avg_r > 230 and avg_g > 230 and avg_b > 230:
            file_info.append((item_id, name, size, "white-bg-skipped", None))
            continue

        # Pillow placeholder detection: very small file size for the saved 800x800
        if size < 45000:
            issues["tiny_file"].append((item_id, name, size, fname))

        # Color match check
        import json as _json
        try:
            colors = _json.loads(colors_json) if colors_json else []
        except Exception:
            colors = []

        if colors:
            primary = colors[0].lower()
            target = COLOR_CENTROIDS.get(primary)
            if target is not None:
                actual = dominant_non_bg_color(img)
                if actual:
                    dist = color_distance(actual, target)
                    # If distance > 130, likely mismatch
                    if dist > 130:
                        # Find what color the actual rgb is closer to
                        closest_color = min(COLOR_CENTROIDS.items(),
                                            key=lambda kv: color_distance(actual, kv[1]))
                        issues["color_mismatch"].append((
                            item_id, name, primary,
                            f"image avg-non-bg = {actual}, closer to '{closest_color[0]}' (dist={dist:.0f})"
                        ))

        file_info.append((item_id, name, size, "ok", colors))

    # Duplicate MD5
    for md5, items in md5_to_items.items():
        if len(items) > 1:
            issues["duplicate_md5"].append((md5, items))

    print("=" * 70)
    print(f"Total items audited: {len(rows)}")
    for category, lst in issues.items():
        if not lst: continue
        print(f"\n── {category} ({len(lst)}) ──")
        for entry in lst[:20]:
            print(f"  {entry}")

    if not issues:
        print("\n✓ No issues found.")


if __name__ == "__main__":
    main()

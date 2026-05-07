"""Demo seeder — English wardrobe + 20 items covering thin→very_thick range.

Usage:
    cd /Users/buttegg/School_Projects/wardrobe_env/closetapp
    .venv/bin/python -m seeder.seed_demo                      # add to current active profile
    .venv/bin/python -m seeder.seed_demo --reset              # wipe items/outfits/etc first
    .venv/bin/python -m seeder.seed_demo --reset --skip-ratings  # no synthetic ratings
"""
import argparse
import random
from pathlib import Path
from sqlalchemy import delete
from PIL import Image, ImageDraw

from app import crud, models, config
from app.database import SessionLocal, Base, engine, run_all_migrations
from app.services.outfit_generator import generate_candidates, persist_candidates


PLACEHOLDER_COLORS = {
    "black": "#222", "white": "#eee", "gray": "#888", "navy": "#1b3c6f",
    "blue": "#3a7bd5", "brown": "#7a4a2b", "beige": "#d8c9a6",
    "green": "#3a7d44", "red": "#c94d4d", "pink": "#f2a3c0",
    "yellow": "#f2d64b", "orange": "#f08a3e", "purple": "#7a4fae",
}


def upload_dir() -> Path:
    """Resolve the active profile's items/images folder."""
    d = config.get_data_dir()
    if d is not None:
        target = d / "items" / "images"
    else:
        target = Path(__file__).resolve().parent.parent / "app" / "static" / "uploads" / "1"
    target.mkdir(parents=True, exist_ok=True)
    return target


def make_placeholder(item_id: int, colors: list) -> str:
    img_dir = upload_dir()
    hex_color = PLACEHOLDER_COLORS.get(colors[0] if colors else "gray", "#bbb")
    img = Image.new("RGB", (200, 200), hex_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 199, 199], outline="#999", width=2)
    fname = f"seed_{item_id}.jpg"
    path = img_dir / fname
    img.save(path, "JPEG", quality=80)
    # If we're in local-first mode, the URL is /images/<fname>
    if config.get_data_dir() is not None:
        return f"/images/{fname}"
    return f"/static/uploads/1/{fname}"


# 20-item English wardrobe spanning very_thin → very_thick.
# Designed to give the outfit generator coverage at any temperature 3–35°C.
DEMO_ITEMS = [
    # ─── Tops: very_thin → very_thick ───
    {"name": "White Tank Top", "category": "top", "layer_role": "inner",
     "thickness": "very_thin", "colors": ["white"], "can_wear_alone": False,
     "material": "cotton", "collar": "crew", "sleeve": "sleeveless",
     "style_tags": ["casual"]},
    {"name": "Black T-Shirt", "category": "top", "layer_role": "mid",
     "thickness": "thin", "colors": ["black"], "can_wear_alone": True,
     "material": "cotton", "collar": "crew", "sleeve": "short",
     "style_tags": ["casual", "minimal"]},
    {"name": "Gray Long Sleeve Tee", "category": "top", "layer_role": "mid",
     "thickness": "thin", "colors": ["gray"], "can_wear_alone": True,
     "material": "cotton", "collar": "crew", "sleeve": "long",
     "style_tags": ["casual"]},
    {"name": "Blue Oxford Shirt", "category": "top", "layer_role": "mid",
     "thickness": "thin", "colors": ["blue"], "can_wear_alone": True,
     "material": "cotton", "collar": "polo", "sleeve": "long",
     "style_tags": ["business", "formal"]},
    {"name": "White Dress Shirt", "category": "top", "layer_role": "mid",
     "thickness": "thin", "colors": ["white"], "can_wear_alone": True,
     "material": "cotton", "collar": "polo", "sleeve": "long",
     "style_tags": ["formal", "business"]},
    {"name": "Beige Knit Sweater", "category": "top", "layer_role": "mid",
     "thickness": "thick", "colors": ["beige"], "can_wear_alone": True,
     "material": "wool", "collar": "crew", "sleeve": "long",
     "style_tags": ["casual"]},
    {"name": "Navy Hoodie", "category": "top", "layer_role": "mid",
     "thickness": "thick", "colors": ["navy"], "can_wear_alone": True,
     "material": "fleece", "collar": "hooded", "sleeve": "long",
     "style_tags": ["casual", "athleisure"]},
    {"name": "Charcoal Suit Jacket", "category": "top", "layer_role": "mid",
     "thickness": "thick", "colors": ["gray"], "can_wear_alone": True,
     "material": "wool", "collar": "polo", "sleeve": "long",
     "style_tags": ["formal", "business"]},
    {"name": "Beige Trench Coat", "category": "top", "layer_role": "outer",
     "thickness": "thick", "colors": ["beige"], "can_wear_alone": True,
     "material": "cotton", "collar": "polo", "top_length": "long", "sleeve": "long",
     "style_tags": ["formal"]},
    {"name": "Black Down Jacket", "category": "top", "layer_role": "outer",
     "thickness": "very_thick", "colors": ["black"], "can_wear_alone": True,
     "material": "nylon", "collar": "hooded", "top_length": "long", "sleeve": "long",
     "style_tags": ["outdoor"]},
    # ─── Bottoms ───
    {"name": "Khaki Shorts", "category": "bottom", "layer_role": "none",
     "thickness": "very_thin", "colors": ["beige"], "can_wear_alone": True,
     "material": "cotton", "pants_fit": "regular", "pants_length": "short",
     "style_tags": ["casual"]},
    {"name": "Dark Jeans", "category": "bottom", "layer_role": "none",
     "thickness": "thick", "colors": ["navy"], "can_wear_alone": True,
     "material": "denim", "pants_fit": "regular", "pants_length": "long",
     "style_tags": ["casual"]},
    {"name": "Black Slacks", "category": "bottom", "layer_role": "none",
     "thickness": "thin", "colors": ["black"], "can_wear_alone": True,
     "material": "polyester", "pants_fit": "slim", "pants_length": "long",
     "style_tags": ["formal", "business"]},
    {"name": "Charcoal Wool Trousers", "category": "bottom", "layer_role": "none",
     "thickness": "thick", "colors": ["gray"], "can_wear_alone": True,
     "material": "wool", "pants_fit": "regular", "pants_length": "long",
     "style_tags": ["formal"]},
    # ─── Shoes ───
    {"name": "White Sneakers", "category": "shoes", "layer_role": "none",
     "thickness": "thin", "colors": ["white"], "can_wear_alone": True,
     "material": "leather", "style_tags": ["casual"]},
    {"name": "Black Leather Shoes", "category": "shoes", "layer_role": "none",
     "thickness": "thin", "colors": ["black"], "can_wear_alone": True,
     "material": "leather", "style_tags": ["formal"]},
    {"name": "Brown Boots", "category": "shoes", "layer_role": "none",
     "thickness": "thick", "colors": ["brown"], "can_wear_alone": True,
     "material": "leather", "style_tags": ["outdoor", "casual"]},
    # ─── Accessories ───
    {"name": "Brown Leather Belt", "category": "accessory", "layer_role": "none",
     "thickness": "very_thin", "colors": ["brown"], "can_wear_alone": True,
     "material": "leather", "style_tags": ["formal"]},
    {"name": "Navy Wool Scarf", "category": "accessory", "layer_role": "none",
     "thickness": "thick", "colors": ["navy"], "can_wear_alone": True,
     "material": "wool", "style_tags": ["outdoor"]},
    {"name": "Black Cap", "category": "accessory", "layer_role": "none",
     "thickness": "thin", "colors": ["black"], "can_wear_alone": True,
     "material": "cotton", "style_tags": ["casual"]},
]


def reset_tables(db):
    for tbl in [
        models.Rating, models.OutfitLog, models.OutfitItem, models.Outfit,
        models.ItemStats, models.ItemState, models.ItemTag, models.Item,
        models.DailyContext, models.ModelRun,
    ]:
        db.execute(delete(tbl))
    db.commit()
    print("Cleared items/outfits/ratings/contexts.")


def seed_items(db, user_id):
    created = 0
    for spec in DEMO_ITEMS:
        data = dict(spec)
        data["category"] = models.CategoryEnum(data["category"])
        data["layer_role"] = models.LayerRoleEnum(data["layer_role"])
        data["thickness"] = models.ThicknessEnum(data["thickness"])
        data.setdefault("style_tags", [])
        item = crud.create_item(db, data, user_id=user_id)
        item.image_path = make_placeholder(item.id, data.get("colors") or [])
        db.commit()
        created += 1
    print(f"Created {created} items (with placeholder images)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="wipe data first")
    parser.add_argument("--skip-ratings", action="store_true",
                        help="don't generate synthetic ratings (recommended; ratings should come from /training)")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        run_all_migrations(db)
        if args.reset:
            reset_tables(db)
        user = crud.get_or_create_default_user(db)
        seed_items(db, user.id)
        active = config.get_active_profile()
        active_name = active["name"] if active else "(none)"
        print(f"\nSeeder done. Active profile: {active_name}")
        print(f"   Items: {len(DEMO_ITEMS)}")
        print(f"   Now visit /training to do calibration (6 batches × 5 outfits)")
    finally:
        db.close()


if __name__ == "__main__":
    main()

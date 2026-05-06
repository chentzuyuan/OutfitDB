"""Migrate existing centralized data → local-first folder structure.

Behavior (per user instruction): keep demo items + images, drop ratings / outfits /
outfit_logs / contexts / item_states / item_stats / model_runs, delete the trained model.
The fresh wardrobe.db will only contain: users, items (and item images).

Note: this is a one-time pre-Phase-3 migration helper, kept for reference.
The legacy DB it migrates *from* was historically named closetmind.db, hence
the file lookup below — that's the real filename on disk, not branding.

Usage:
    .venv/bin/python -m tools.migrate_to_local_first --target ~/MyOutfitDB
    .venv/bin/python -m tools.migrate_to_local_first --target ~/MyOutfitDB --reset
"""
import argparse
import shutil
import sqlite3
from pathlib import Path
from typing import List

from app import config, database, models, crud


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEGACY_DB = PROJECT_ROOT / "data" / "closetmind.db"
LEGACY_UPLOADS = PROJECT_ROOT / "app" / "static" / "uploads" / "1"
LEGACY_MODEL = PROJECT_ROOT / "models" / "user_1_model.json"


def fetch_legacy_items(legacy_db_path: Path) -> List[dict]:
    """Read items + the matching state/stats rows from the legacy SQLite file."""
    if not legacy_db_path.exists():
        print(f"WARN: legacy DB not found: {legacy_db_path}")
        return []
    conn = sqlite3.connect(str(legacy_db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM items WHERE is_active = 1"
    ).fetchall()
    items = [dict(r) for r in rows]
    conn.close()
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="目標母資料夾，例如 ~/MyOutfitDB")
    parser.add_argument("--reset", action="store_true",
                        help="若 wardrobe.db 已存在則先刪掉重建")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    print(f"Target folder: {target}")

    # 1. 建立資料夾結構
    config.ensure_data_dir_structure(target)
    print("Folder structure created.")

    # 2. 重置 wardrobe.db（如果指定 --reset）
    db_path = target / "wardrobe.db"
    if args.reset and db_path.exists():
        db_path.unlink()
        print(f"↻ 已刪除舊的 {db_path}")

    # 3. 寫 config.json 並重新綁定 engine
    config.save_config({"data_dir": str(target)})
    database.rebind_engine_to_current_config()

    # 4. 建表 + 預設使用者
    models.Base.metadata.create_all(bind=database.engine)
    db = database.SessionLocal()
    try:
        crud.get_or_create_default_user(db)
    finally:
        db.close()
    print("wardrobe.db schema created (with users).")

    # 5. 讀舊 items 並寫進新 DB
    items = fetch_legacy_items(LEGACY_DB)
    if not items:
        print("WARN: legacy DB has no items to migrate; exiting.")
        return

    db = database.SessionLocal()
    try:
        copied = 0
        skipped = 0
        for it in items:
            data = {
                "name": it["name"],
                "category": models.CategoryEnum(it["category"]),
                "layer_role": models.LayerRoleEnum(it["layer_role"]),
                "colors": _maybe_json(it.get("colors")) or [],
                "is_multicolor": bool(it.get("is_multicolor")),
                "pattern": it.get("pattern") or "solid",
                "pattern_complexity": it.get("pattern_complexity") or 0,
                "material": it.get("material") or "cotton",
                "thickness": models.ThicknessEnum(it["thickness"]),
                "style_tags": _maybe_json(it.get("style_tags")) or [],
                "can_wear_alone": bool(it.get("can_wear_alone")),
                "shoulder_fit": it.get("shoulder_fit"),
                "waist_fit": it.get("waist_fit"),
                "collar": it.get("collar"),
                "top_length": it.get("top_length"),
                "pants_length": it.get("pants_length"),
                "pants_fit": it.get("pants_fit"),
            }
            new_item = crud.create_item(db, data, user_id=1)
            # 處理圖片
            old_path = it.get("image_path") or ""
            old_filename = _extract_filename(old_path)
            if old_filename:
                src = LEGACY_UPLOADS / old_filename
                if src.exists():
                    new_filename = f"{new_item.id}_{old_filename}"
                    dst = target / "items" / "images" / new_filename
                    shutil.copy2(src, dst)
                    new_item.image_path = f"/images/{new_filename}"
                    db.commit()
                    db.refresh(new_item)
                    copied += 1
                else:
                    skipped += 1
        print(f"Copied {len(items)} items ({copied} images moved, {skipped} originals not found).")
    finally:
        db.close()

    # 6. 確認舊模型不要帶過來（依使用者指示砍訓練資料）
    new_model = target / "models" / "current.json"
    if new_model.exists():
        new_model.unlink()
        print("↻ 移除舊模型（依指示砍掉訓練資料）")

    print("\nMigration complete.")
    print(f"   config.json → {config.CONFIG_FILE}")
    print(f"   data_dir   → {target}")
    print("\n下一步：重啟 server (uvicorn app.main:app --reload) 後到 / 應該不會被導向 /setup")


def _maybe_json(val):
    import json
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return None
    return None


def _extract_filename(image_path: str) -> str:
    if not image_path:
        return ""
    return Path(image_path).name


if __name__ == "__main__":
    main()

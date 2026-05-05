#!/usr/bin/env python3
"""Sync slot files to all processed DB-referenced files via name lookup.

Unlike v1 which mapped slot->id directly, this version resolves slot->item
by NAME (from /tmp/wardrobe_spec.json), which handles the case where items
were re-imported with new IDs.
"""
import json
import sqlite3
from pathlib import Path
from PIL import Image

DB = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/wardrobe.db")
IMG_DIR = Path("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/items/images")
SPECS = [Path("/tmp/wardrobe_spec.json"), Path("/tmp/wardrobe_spec_extra.json"), Path("/tmp/wardrobe_spec_tux.json"), Path("/tmp/wardrobe_spec_cold_formal.json")]

# Aggregate spec
all_items = []
for s in SPECS:
    if s.exists():
        all_items.extend(json.loads(s.read_text()))
by_slot = {it["slot"]: it for it in all_items}

conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT id, name, image_path FROM items")
rows = cur.fetchall()
by_name = {name: (id_, path) for id_, name, path in rows}

ok = 0
missing = []
for slot, it in by_slot.items():
    name = it["name"]
    if name not in by_name:
        missing.append((slot, name, "no DB row"))
        continue
    id_, path = by_name[name]
    if not path or not path.startswith("/images/"):
        missing.append((slot, name, f"bad path {path}"))
        continue
    src = IMG_DIR / f"{slot}.jpg"
    if not src.exists():
        missing.append((slot, name, "source missing"))
        continue
    processed = IMG_DIR / Path(path).name
    img = Image.open(src).convert("RGB")
    img.thumbnail((800, 800))
    img.save(processed, "JPEG", quality=85)
    print(f"slot {slot:>2} ({name[:30]:<30}) -> {processed.name}")
    ok += 1

conn.close()
print(f"\nUpdated {ok} processed copies; {len(missing)} issues:")
for slot, name, err in missing:
    print(f"  slot {slot} ({name}): {err}")

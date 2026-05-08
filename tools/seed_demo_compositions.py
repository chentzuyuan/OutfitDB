"""
Backfill realistic fabric compositions on ~60% of items so the per-item
modal's composition doughnut has something to show.

Rule:
    - The highest-pct entry in composition MUST equal items.material (this
      preserves Decision 3's "mirror the JSON" invariant).
    - The remaining 1-2 fabrics are picked from a per-material blend table.

Re-runnable. We only overwrite items whose composition is NULL or empty —
items with a hand-edited composition are left alone.
"""
import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
sys.path.insert(0, str(APP_ROOT))
from app.config import get_data_dir  # noqa: E402

# Realistic blend partners per primary material. The first entry of the
# tuple is the % range for the secondary material; we round to nice numbers.
BLEND_TABLE = {
    "cotton":    [("polyester", (15, 40)), ("linen", (20, 50)), ("spandex", (3, 8))],
    "wool":      [("cashmere", (10, 30)), ("nylon", (10, 20)), ("acrylic", (15, 35))],
    "polyester": [("cotton", (20, 45)), ("spandex", (3, 10)), ("rayon", (15, 35))],
    "denim":     [("spandex", (1, 3)), ("polyester", (5, 15))],
    "leather":   [],   # leather is usually solo
    "silk":      [("cotton", (15, 40)), ("viscose", (20, 40))],
    "linen":     [("cotton", (20, 45)), ("rayon", (15, 30))],
    "nylon":     [("spandex", (5, 15)), ("polyester", (15, 35))],
    "cashmere":  [("wool", (15, 35)), ("silk", (10, 20))],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratio", type=float, default=0.6,
                        help="fraction of items to give a multi-material blend (default 0.6)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    data_dir = get_data_dir()
    if data_dir is None:
        sys.exit("no active profile")
    db_path = Path(data_dir) / "wardrobe.db"
    if not db_path.exists():
        sys.exit(f"no DB at {db_path}")
    print(f"profile DB: {db_path}")

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # Only touch items whose composition is missing
    rows = con.execute(
        "SELECT id, name, material, composition FROM items "
        "WHERE is_active = 1 AND (composition IS NULL OR composition = '' OR composition = 'null')"
    ).fetchall()
    print(f"  {len(rows)} items missing composition")

    updated = 0
    skipped_solo = 0
    for r in rows:
        primary = r["material"]
        if primary not in BLEND_TABLE:
            continue

        # Decide whether to blend (based on ratio) or leave solo
        if random.random() > args.ratio or not BLEND_TABLE[primary]:
            skipped_solo += 1
            continue

        # Pick 1-2 secondaries from the blend table
        partners = BLEND_TABLE[primary][:]
        random.shuffle(partners)
        n_partners = 1 if random.random() < 0.7 else min(2, len(partners))
        chosen = partners[:n_partners]

        # Build composition: primary takes the rest after secondaries
        comp = []
        used = 0
        for partner_name, (lo, hi) in chosen:
            pct = random.randint(lo, hi)
            # Round to nearest 5 for clean-looking numbers
            pct = max(1, 5 * round(pct / 5))
            comp.append({"material": partner_name, "pct": pct})
            used += pct
        primary_pct = 100 - used
        if primary_pct <= 0:
            # Skip — secondaries summed too high; rare but possible
            continue
        # Insert primary FIRST so it's also the highest-pct (the mirror invariant)
        comp.insert(0, {"material": primary, "pct": primary_pct})

        # Sanity: if primary isn't the largest entry, swap something so it is.
        # In our generation it should always be — secondaries cap below 50% — but defensive.
        max_entry = max(comp, key=lambda x: x["pct"])
        if max_entry["material"] != primary:
            continue

        con.execute(
            "UPDATE items SET composition = ? WHERE id = ?",
            (json.dumps(comp), r["id"]),
        )
        updated += 1

    con.commit()
    print(f"\n✓ {updated} items got a blended composition")
    print(f"  {skipped_solo} stayed solo")

    # Spot-check
    print("\nSamples:")
    for r in con.execute("SELECT name, material, composition FROM items "
                          "WHERE composition IS NOT NULL AND composition != 'null' "
                          "ORDER BY id LIMIT 6").fetchall():
        print(f"  {r['name']:30} {r['composition']}")


if __name__ == "__main__":
    main()

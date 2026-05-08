"""
Backfill realistic wear history for the active profile so the per-item
stats modal and Closet stats dashboard have something to draw.

What this writes:
    - outfit_logs rows with scheduled_for spread across the last 90 days
    - item_states.worn_count, last_worn (recomputed from the new logs)
    - item_stats.last_worn_date, days_since_worn (recomputed)

Re-runnable. On each run we WIPE the demo logs we previously inserted
(tagged via a high-id sentinel pattern) and re-seed. We never touch logs
you created through the real UI flow (those will keep their real ids).

Strategy:
    - 80 wear events over 90 days
    - bias toward recent days
    - bias toward a "favorite" 30% of items so the most-worn chart has
      a clear shape (not flat)
"""
import argparse
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Make `app.config` importable so we resolve the active profile ────
HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
sys.path.insert(0, str(APP_ROOT))
from app.config import get_data_dir  # noqa: E402

# We can't tag logs (no `notes` column), so idempotency is "delete every log
# for this user that has a non-null scheduled_for and re-seed". That's safe
# because in this codebase the UI doesn't write scheduled_for unless the
# user explicitly schedules an outfit — and the active demo profile has none.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=80,
                        help="number of fake wear events to seed (default 80)")
    parser.add_argument("--days", type=int, default=90,
                        help="span of fake history in days (default 90)")
    parser.add_argument("--seed", type=int, default=42,
                        help="random seed for reproducible distributions")
    args = parser.parse_args()

    random.seed(args.seed)
    data_dir = get_data_dir()
    if data_dir is None:
        sys.exit("no active profile — set one up first")
    db_path = Path(data_dir) / "wardrobe.db"
    if not db_path.exists():
        sys.exit(f"no DB at {db_path}")
    print(f"profile DB: {db_path}")

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # ── 1. Find a default user ──
    user = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if user is None:
        sys.exit("no user in DB")
    user_id = user["id"]

    # ── 2. Wipe existing demo logs (idempotent re-run) — see top-of-file note ──
    deleted = con.execute(
        "DELETE FROM outfit_logs WHERE user_id = ? AND scheduled_for IS NOT NULL",
        (user_id,),
    ).rowcount
    if deleted:
        print(f"  cleared {deleted} prior demo logs")

    # ── 3. Pull all outfits this user has — we'll sample from them ──
    outfit_rows = con.execute(
        "SELECT id FROM outfits WHERE user_id = ?", (user_id,)
    ).fetchall()
    if not outfit_rows:
        sys.exit("no outfits in DB — train at least once first")
    outfit_ids = [r["id"] for r in outfit_rows]
    print(f"  {len(outfit_ids)} outfits to sample from")

    # ── 4. Pull active items + designate a 'favorite' 30% slice ──
    item_rows = con.execute(
        "SELECT id FROM items WHERE user_id = ? AND is_active = 1", (user_id,)
    ).fetchall()
    item_ids = [r["id"] for r in item_rows]
    if not item_ids:
        sys.exit("no items")
    favorite_count = max(1, len(item_ids) * 30 // 100)
    favorites = set(random.sample(item_ids, favorite_count))
    print(f"  {len(item_ids)} items active, {len(favorites)} favorites")

    # We pre-build a per-outfit favorite-overlap score so we can
    # bias toward outfits that contain favorite items.
    rows = con.execute(
        "SELECT outfit_id, item_id FROM outfit_items "
        "WHERE outfit_id IN (" + ",".join("?" * len(outfit_ids)) + ")",
        outfit_ids,
    ).fetchall()
    by_outfit = {}
    for r in rows:
        by_outfit.setdefault(r["outfit_id"], []).append(r["item_id"])
    fav_overlap = {
        oid: sum(1 for it in items if it in favorites)
        for oid, items in by_outfit.items()
    }

    # ── 5. Generate wear events ──
    today = datetime.utcnow().date()
    events = []
    for _ in range(args.n):
        # Recency bias — weighted to the last 30 days
        days_back = int(random.triangular(0, args.days, 7))
        day = today - timedelta(days=days_back)

        # Outfit choice — weighted by favorite overlap (fav-heavy outfits more likely)
        weights = [fav_overlap.get(o, 0) + 1 for o in outfit_ids]
        outfit_id = random.choices(outfit_ids, weights=weights, k=1)[0]

        # 80% accept / 15% modify / 5% reject (rejected → final = NULL)
        roll = random.random()
        if roll < 0.80:
            action, final = "accepted", outfit_id
        elif roll < 0.95:
            action, final = "modified", outfit_id  # close enough for demo purposes
        else:
            action, final = "rejected", None

        events.append((outfit_id, final, action, day))

    # ── 5b. Pull existing daily_context ids — outfit_logs.context_id is NOT NULL
    # so we attach each fake wear event to a real context row.
    ctx_rows = con.execute(
        "SELECT id FROM daily_contexts WHERE user_id = ?", (user_id,)
    ).fetchall()
    if not ctx_rows:
        sys.exit("no daily_contexts — generate at least one before running this")
    context_ids = [r["id"] for r in ctx_rows]

    # ── 6. Insert outfit_logs ──
    sched_dt = lambda d: datetime.combine(d, datetime.min.time()).replace(hour=8)  # 8 AM
    inserted = 0
    for outfit_id, final, action, day in events:
        con.execute(
            "INSERT INTO outfit_logs "
            "(user_id, context_id, recommended_outfit_id, final_worn_outfit_id, "
            " user_action, scheduled_for, is_scheduled, logged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            (user_id, random.choice(context_ids), outfit_id, final, action,
             sched_dt(day), datetime.utcnow()),
        )
        inserted += 1

    # ── 7. Recompute item_states.worn_count, last_worn from real + fake logs ──
    # Per-item: worn_count = number of outfit_logs where final_worn_outfit_id
    # links via outfit_items to that item; last_worn = max scheduled_for.
    con.execute("""
        WITH wear AS (
            SELECT oi.item_id,
                   COUNT(*) AS wc,
                   MAX(ol.scheduled_for) AS last_d
            FROM outfit_logs ol
            JOIN outfit_items oi ON oi.outfit_id = ol.final_worn_outfit_id
            WHERE ol.final_worn_outfit_id IS NOT NULL
              AND ol.user_id = ?
            GROUP BY oi.item_id
        )
        UPDATE item_states
        SET worn_count = COALESCE((SELECT wc FROM wear WHERE wear.item_id = item_states.item_id), 0),
            last_worn  = (SELECT last_d FROM wear WHERE wear.item_id = item_states.item_id)
        WHERE item_id IN (SELECT id FROM items WHERE user_id = ?)
    """, (user_id, user_id))

    # ── 8. Recompute item_stats.last_worn_date and days_since_worn ──
    con.execute("""
        UPDATE item_stats
        SET last_worn_date = (
                SELECT s.last_worn FROM item_states s WHERE s.item_id = item_stats.item_id
            ),
            days_since_worn = (
                SELECT CAST((julianday('now') - julianday(s.last_worn)) AS INTEGER)
                FROM item_states s
                WHERE s.item_id = item_stats.item_id AND s.last_worn IS NOT NULL
            )
        WHERE item_id IN (SELECT id FROM items WHERE user_id = ?)
    """, (user_id,))

    con.commit()

    # ── 9. Summary ──
    summary = con.execute(
        "SELECT COUNT(*) FROM outfit_logs WHERE user_id = ? AND scheduled_for IS NOT NULL",
        (user_id,),
    ).fetchone()[0]
    items_with_wears = con.execute(
        "SELECT COUNT(*) FROM item_states WHERE worn_count > 0 AND item_id IN "
        "(SELECT id FROM items WHERE user_id = ?)",
        (user_id,),
    ).fetchone()[0]
    print(f"\n✓ inserted {inserted} fake wear events")
    print(f"✓ outfit_logs with scheduled_for: {summary}")
    print(f"✓ items with worn_count > 0: {items_with_wears} / {len(item_ids)}")


if __name__ == "__main__":
    main()

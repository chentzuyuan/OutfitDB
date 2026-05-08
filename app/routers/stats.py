from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import crud, models
from ..database import get_db
from ..services.model_training import get_active_model_path


router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/home")
def home_stats(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    counts = {c.value: 0 for c in models.CategoryEnum}
    for cat in models.CategoryEnum:
        counts[cat.value] = (
            db.query(models.Item)
            .filter(
                models.Item.user_id == user.id,
                models.Item.is_active == True,
                models.Item.category == cat,
            )
            .count()
        )
    ratings_count = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user.id)
        .count()
    )
    has_model = get_active_model_path().exists()
    has_location = user.lat is not None and user.lon is not None
    training_complete = bool(user.training_complete)
    training_ratings = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user.id, models.Rating.ideal_temp_zone.isnot(None))
        .count()
    )
    training_batches_done = training_ratings // 5
    training_batches_total = 6
    temp_offset = float(user.temp_offset or 0.0)

    # need (top OR fullbody) + (bottom OR fullbody) + shoes
    has_upper = counts["top"] >= 1 or counts["fullbody"] >= 1
    has_lower = counts["bottom"] >= 1 or counts["fullbody"] >= 1
    has_min_items = has_upper and has_lower and counts["shoes"] >= 1

    return {
        "items": counts,
        "ratings_count": ratings_count,
        "has_model": has_model,
        "has_location": has_location,
        "training_complete": training_complete,
        "training_batches_done": training_batches_done,
        "training_batches_total": training_batches_total,
        "temp_offset": temp_offset,
        "checklist": {
            "min_items": has_min_items,
            "training_complete": training_complete,
            "model_trained": has_model,
            "location_set": has_location,
        },
        # Recommendation only unlocks after BOTH min_items AND training are done
        "ready_to_recommend": has_min_items and training_complete,
    }


# ─────────────────────────────────────────────────────────────────────
# /stats/charts — feeds the Closet Stats dashboard with aggregations
# the front-end Chart.js renders. Each section is a flat dict so the
# template can pass it directly to a chart constructor.
# ─────────────────────────────────────────────────────────────────────
@router.get("/charts")
def chart_data(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    items = (
        db.query(models.Item)
        .filter(models.Item.user_id == user.id, models.Item.is_active == True)  # noqa: E712
        .all()
    )

    # 1. Category distribution
    category_counts = Counter()
    for it in items:
        if it.category is not None:
            category_counts[it.category.value] += 1
    category_chart = {
        "labels": [c.value for c in models.CategoryEnum],
        "values": [category_counts.get(c.value, 0) for c in models.CategoryEnum],
    }

    # 2. Color distribution (colors is a JSON list — flatten across items)
    color_counts = Counter()
    for it in items:
        for c in (it.colors or []):
            color_counts[str(c)] += 1
    color_top = color_counts.most_common(10)
    color_chart = {
        "labels": [c[0] for c in color_top],
        "values": [c[1] for c in color_top],
    }

    # 3. Material distribution (the indexed mirror — Decision 3 left block)
    material_counts = Counter()
    for it in items:
        if it.material:
            material_counts[it.material] += 1
    material_top = material_counts.most_common(10)
    material_chart = {
        "labels": [m[0] for m in material_top],
        "values": [m[1] for m in material_top],
    }

    # 4. Wear-count distribution — top-10 most-worn items (item_states.wear_count)
    wear_rows = (
        db.query(models.Item.name, models.ItemState.worn_count)
        .join(models.ItemState, models.ItemState.item_id == models.Item.id)
        .filter(
            models.Item.user_id == user.id,
            models.Item.is_active == True,  # noqa: E712
        )
        .order_by(models.ItemState.worn_count.desc().nullslast())
        .limit(10)
        .all()
    )
    wear_chart = {
        "labels": [name for name, _ in wear_rows],
        "values": [int(c or 0) for _, c in wear_rows],
    }

    # 5. Coverage — how many outfits each item appears in (item_stats.coverage_count)
    cov_rows = (
        db.query(models.Item.name, models.ItemStats.coverage_count)
        .join(models.ItemStats, models.ItemStats.item_id == models.Item.id)
        .filter(
            models.Item.user_id == user.id,
            models.Item.is_active == True,  # noqa: E712
        )
        .order_by(models.ItemStats.coverage_count.desc().nullslast())
        .limit(10)
        .all()
    )
    coverage_chart = {
        "labels": [name for name, _ in cov_rows],
        "values": [int(c or 0) for _, c in cov_rows],
    }

    # 6. Aesthetic rating distribution — int -1 / 0 / 1 / 2
    rating_rows = (
        db.query(models.Rating.rating, func.count(models.Rating.id))
        .filter(models.Rating.user_id == user.id)
        .group_by(models.Rating.rating)
        .all()
    )
    rating_buckets = {-1: 0, 0: 0, 1: 0, 2: 0}
    for score, n in rating_rows:
        if score in rating_buckets:
            rating_buckets[score] = int(n)
    rating_chart = {
        "labels": ["−1 dislike", "0 meh", "1 like", "2 love"],
        "values": [rating_buckets[-1], rating_buckets[0], rating_buckets[1], rating_buckets[2]],
    }

    # 7. Headline numbers
    headline = {
        "total_items": len(items),
        "total_ratings": db.query(models.Rating).filter(models.Rating.user_id == user.id).count(),
        "total_temp_ratings": db.query(models.TemperatureRating).filter(models.TemperatureRating.user_id == user.id).count(),
        "total_occ_ratings": db.query(models.OccasionRating).filter(models.OccasionRating.user_id == user.id).count(),
        "total_outfits": db.query(models.Outfit).filter(models.Outfit.user_id == user.id).count(),
    }

    return {
        "headline": headline,
        "category": category_chart,
        "color": color_chart,
        "material": material_chart,
        "wear": wear_chart,
        "coverage": coverage_chart,
        "rating": rating_chart,
    }

from fastapi import APIRouter, Depends
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

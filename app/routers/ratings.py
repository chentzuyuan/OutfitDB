from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..database import get_db


router = APIRouter(prefix="/ratings", tags=["ratings"])


@router.post("/", response_model=schemas.RatingOut)
def submit_rating(payload: schemas.RatingCreate, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    outfit = db.get(models.Outfit, payload.outfit_id)
    if outfit is None:
        raise HTTPException(status_code=404, detail="outfit not found")
    rating = crud.upsert_rating(
        db,
        user_id=user.id,
        outfit_id=payload.outfit_id,
        rating=payload.rating,
        source=payload.rating_source,
    )
    crud.update_item_stats_for_outfit(db, payload.outfit_id)
    return rating

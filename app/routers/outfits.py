from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import crud, models, schemas
from ..database import get_db
from ..services.outfit_generator import generate_candidates, persist_candidates, build_outfit_from_items


router = APIRouter(prefix="/outfits", tags=["outfits"])


def _outfit_to_out(o: models.Outfit) -> dict:
    return {
        "id": o.id,
        "user_id": o.user_id,
        "context_id": o.context_id,
        "warmth_score": o.warmth_score,
        "inner_count": o.inner_count,
        "mid_count": o.mid_count,
        "outer_count": o.outer_count,
        "total_items": o.total_items,
        "is_generated": o.is_generated,
        "coverage_curve": o.coverage_curve,
        "aesthetic_curve": o.aesthetic_curve,
        "optimal_layer_count": o.optimal_layer_count,
        "overkill_layers": o.overkill_layers,
        "underfit_flag": o.underfit_flag,
        "aesthetic_stop_layers": o.aesthetic_stop_layers,
        "outfit_items": [
            {
                "item_id": oi.item_id,
                "position": oi.position,
                "item": crud.item_to_out(oi.item) if oi.item else None,
            }
            for oi in o.outfit_items
        ],
    }


@router.post("/generate")
def generate(payload: schemas.GenerateRequest, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    context = db.get(models.DailyContext, payload.context_id)
    if context is None:
        raise HTTPException(status_code=404, detail="context not found")
    candidates = generate_candidates(
        db, context, user.id, n=payload.n,
        must_include_item_id=payload.must_include_item_id,
    )
    if not candidates:
        raise HTTPException(status_code=400, detail="Could not generate candidate outfits (not enough items or constraints too strict)")
    saved = persist_candidates(db, candidates, context.id, user.id)
    return {"count": len(saved), "outfit_ids": [o.id for o in saved]}


@router.get("/")
def list_outfits(
    context_id: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(models.Outfit)
    if context_id is not None:
        q = q.filter(models.Outfit.context_id == context_id)
    outfits = q.order_by(desc(models.Outfit.id)).limit(limit).all()
    return [_outfit_to_out(o) for o in outfits]


@router.get("/{outfit_id}")
def get_outfit(outfit_id: int, db: Session = Depends(get_db)):
    o = db.get(models.Outfit, outfit_id)
    if o is None:
        raise HTTPException(status_code=404, detail="outfit not found")
    return _outfit_to_out(o)


@router.post("/manual")
def create_manual_outfit(payload: schemas.ManualOutfitCreate, db: Session = Depends(get_db)):
    """User-curated outfit (修改後接受流程)."""
    user = crud.get_or_create_default_user(db)
    context = db.get(models.DailyContext, payload.context_id)
    if context is None:
        raise HTTPException(status_code=404, detail="context not found")
    if not payload.item_ids:
        raise HTTPException(status_code=400, detail="item_ids cannot be empty")
    items = []
    for iid in payload.item_ids:
        it = db.get(models.Item, iid)
        if it is None or it.user_id != user.id or not it.is_active:
            raise HTTPException(status_code=400, detail=f"item {iid} is invalid")
        items.append(it)

    spec = build_outfit_from_items(items, context)
    if spec is None:
        raise HTTPException(status_code=400, detail="Cannot form a valid outfit (need at least 1 bottom; top count limited by layer rules)")

    saved = persist_candidates(db, [spec], context.id, user.id)
    outfit = saved[0]
    outfit.is_generated = False  # mark as user-curated
    db.commit()
    db.refresh(outfit)
    return _outfit_to_out(outfit)

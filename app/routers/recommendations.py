from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import crud, models, schemas
from ..database import get_db
from ..services.outfit_generator import generate_candidates, persist_candidates
from ..services.scoring import get_top_k_recommendations
from .outfits import _outfit_to_out


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("/")
def recommend(payload: schemas.RecommendRequest, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    context = db.get(models.DailyContext, payload.context_id)
    if context is None:
        raise HTTPException(status_code=404, detail="context not found")

    # 生成新的候選並寫入（避免重複使用舊候選）
    candidates = generate_candidates(
        db, context, user.id, n=100,
        must_include_item_ids=payload.must_include_item_ids,
        bypass_pinned_legality=payload.bypass_pinned_legality,
    )
    if not candidates:
        # Distinguish two failure modes so the frontend can show a
        # specific "Try anyway" override only when it makes sense:
        #   (a) force-include set itself violates a hard rule (2 outers etc.)
        #       → recoverable via bypass, return error_code=force_include_illegal
        #   (b) wardrobe genuinely can't fill the pool (too few items, all
        #       too warm/cool, etc.) → bypass won't help, generic error
        ids = payload.must_include_item_ids or []
        is_force_include_problem = bool(ids) and not payload.bypass_pinned_legality
        if is_force_include_problem:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "force_include_illegal",
                    "message": "Your pinned items couldn't be combined into a normal-looking outfit for this weather/occasion. This usually means: (a) two outer pieces, (b) too warm for the temperature, or (c) the styling rules block the combo. Click 'Try anyway' to skip those checks.",
                },
            )
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "no_candidates",
                "message": "Could not generate candidate outfits — wardrobe may be too small or all items fail the temperature gate.",
            },
        )
    outfits = persist_candidates(db, candidates, context.id, user.id)

    scored = get_top_k_recommendations(db, outfits, context, user.id, top_k=payload.top_k)

    # 只對 Top-K 的 items 增加 coverage_count
    for o, _ in scored:
        crud.increment_coverage_for_outfit(db, o.id)

    return [
        {
            "outfit": _outfit_to_out(o),
            **scores,
        }
        for o, scores in scored
    ]


@router.post("/log")
def log_outfit(payload: schemas.OutfitLogCreate, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    context = db.get(models.DailyContext, payload.context_id)
    if context is None:
        raise HTTPException(status_code=404, detail="context not found")

    log = models.OutfitLog(
        user_id=user.id,
        context_id=payload.context_id,
        recommended_outfit_id=payload.recommended_outfit_id,
        final_worn_outfit_id=payload.final_worn_outfit_id,
        user_action=payload.user_action,
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # 評分權重：
    #   accepted = +1 (照穿)
    #   modified = +2 (使用者主動 curate 過的組合，強信號)
    #   rejected = -1 (這套不行)
    auto_rating: dict[models.UserActionEnum, int] = {
        models.UserActionEnum.accepted: 1,
        models.UserActionEnum.modified: 2,
        models.UserActionEnum.rejected: -1,
    }

    if payload.final_worn_outfit_id is not None:
        crud.mark_outfit_worn(db, payload.final_worn_outfit_id)

    if payload.user_action in auto_rating:
        target_outfit = (
            payload.final_worn_outfit_id
            if payload.user_action != models.UserActionEnum.rejected
            else payload.recommended_outfit_id
        )
        if target_outfit is not None:
            crud.upsert_rating(
                db,
                user_id=user.id,
                outfit_id=target_outfit,
                rating=auto_rating[payload.user_action],
                source=models.RatingSourceEnum.user_modified,
            )
            crud.update_item_stats_for_outfit(db, target_outfit)

    return {"ok": True, "log_id": log.id}

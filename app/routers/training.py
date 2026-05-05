"""Training (calibration) router.

Onboarding flow:
- 6 batches × 5 outfits = 30 ratings before app fully unlocks.
- Each rating captures: aesthetic (-1/0/1) + ideal_temp_zone.
- After every batch (5 ratings), recompute users.temp_offset and retrain XGBoost.

The /recommend page applies users.temp_offset when scoring context_fit.
"""
from datetime import datetime, date as date_cls
import random
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..database import get_db
from ..services.outfit_generator import generate_candidates, persist_candidates


router = APIRouter(prefix="/training", tags=["training"])


REQUIRED_BATCHES = 6
BATCH_SIZE = 5


# ─── Temperature zones ──────────────────────────────────────────────────
# Each zone has a representative temperature used for context generation
# AND for computing temp_offset deltas later.
TEMP_ZONES = ["cold", "cool", "mild", "warm", "hot"]
ZONE_TO_TEMP = {
    "cold": 5.0,    # <10°C
    "cool": 14.0,   # 10–18°C
    "mild": 22.0,   # 18–25°C
    "warm": 27.0,   # 25–30°C
    "hot": 32.0,    # >30°C
}


def _temp_to_zone(t: float) -> str:
    if t < 10: return "cold"
    if t < 18: return "cool"
    if t < 25: return "mild"
    if t < 30: return "warm"
    return "hot"


# ─── Schemas ────────────────────────────────────────────────────────────
class TrainingProgress(BaseModel):
    completed: bool
    ratings_done: int
    batches_done: int      # full batches (5 ratings each)
    total_batches: int
    next_batch_idx: int    # 0-indexed for next batch
    target_temp: Optional[float] = None  # the random temp this next batch will use


class TrainingOutfitOut(BaseModel):
    outfit_id: int
    items: List[dict]
    coverage_curve: List[float]
    aesthetic_curve: List[float]
    optimal_layer_count: Optional[int]


class TrainingBatchOut(BaseModel):
    batch_idx: int
    target_temp: float
    target_zone: str
    outfits: List[TrainingOutfitOut]
    progress: TrainingProgress


class RatingItem(BaseModel):
    outfit_id: int
    rating: int = Field(..., ge=-1, le=1)  # -1 / 0 / 1
    ideal_temp_zone: str  # one of TEMP_ZONES


class BatchSubmit(BaseModel):
    target_temp: float
    ratings: List[RatingItem]


# ─── Helpers ────────────────────────────────────────────────────────────
def _progress(db: Session, user_id: int) -> TrainingProgress:
    user = crud.get_or_create_default_user(db)
    n = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user_id, models.Rating.ideal_temp_zone.isnot(None))
        .count()
    )
    batches = n // BATCH_SIZE
    completed = bool(user.training_complete)
    return TrainingProgress(
        completed=completed,
        ratings_done=n,
        batches_done=batches,
        total_batches=REQUIRED_BATCHES,
        next_batch_idx=min(batches, REQUIRED_BATCHES),
    )


def _make_temp_context(db: Session, user_id: int, temp: float) -> models.DailyContext:
    """Create (or reuse) a synthetic DailyContext at the target temperature.
    Uses a per-day-fresh datetime offset so we don't collide with real /contexts.
    """
    # Use today's date with a microsecond offset to avoid the unique constraint
    base = datetime.utcnow().replace(microsecond=0)
    # marker: nudge minute by hash so each batch context is distinct
    distinct = base.replace(minute=int(temp * 10) % 60, second=int(temp) % 60)
    ctx = models.DailyContext(
        user_id=user_id,
        date=distinct,
        temperature=temp,
        temperature_high=temp + 3,
        temperature_low=temp - 3,
        weather=models.WeatherEnum.cloudy,
        occasion=models.OccasionEnum.casual,
        notes=f"[training] batch ctx @ {temp}°C",
    )
    db.add(ctx)
    db.commit()
    db.refresh(ctx)
    return ctx


def _outfit_to_dict(o: models.Outfit) -> dict:
    return {
        "outfit_id": o.id,
        "items": [
            {
                "item_id": oi.item_id,
                "position": oi.position,
                "item": crud.item_to_out(oi.item) if oi.item else None,
            }
            for oi in o.outfit_items
        ],
        "coverage_curve": o.coverage_curve or [],
        "aesthetic_curve": o.aesthetic_curve or [],
        "optimal_layer_count": o.optimal_layer_count,
    }


# ─── Routes ─────────────────────────────────────────────────────────────
@router.get("/progress", response_model=TrainingProgress)
def progress(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    return _progress(db, user.id)


@router.get("/next_batch")
def next_batch(db: Session = Depends(get_db)):
    """Generate a fresh batch of BATCH_SIZE outfits at a random temperature.
    After calibration (training_complete=True), additional batches are
    allowed for ongoing model refinement — they keep adding ratings and
    retraining without re-gating the user."""
    user = crud.get_or_create_default_user(db)

    # Random temp in [3, 35]°C — retry up to 6 times if wardrobe can't cover that temp
    candidates = []
    target_temp = None
    target_zone = None
    ctx = None
    for _ in range(6):
        target_temp = round(random.uniform(3.0, 35.0), 1)
        target_zone = _temp_to_zone(target_temp)
        ctx = _make_temp_context(db, user.id, target_temp)
        candidates = generate_candidates(db, ctx, user.id, n=BATCH_SIZE * 4)
        if candidates:
            break
    if not candidates:
        raise HTTPException(400, "Could not generate outfits (wardrobe doesn't cover this temperature; add items for extreme weather)")
    saved = persist_candidates(db, candidates[:BATCH_SIZE], ctx.id, user.id)

    progress_obj = _progress(db, user.id)
    return {
        "batch_idx": progress_obj.batches_done,
        "context_id": ctx.id,
        "target_temp": target_temp,
        "target_zone": target_zone,
        "outfits": [_outfit_to_dict(o) for o in saved],
        "progress": progress_obj.model_dump(),
    }


@router.post("/submit_batch")
def submit_batch(payload: BatchSubmit, db: Session = Depends(get_db)):
    """Save 5 ratings, recompute temp_offset, retrain (background-safe).
    Works both during initial calibration and for additional practice
    batches after calibration is complete."""
    user = crud.get_or_create_default_user(db)
    if len(payload.ratings) < 1:
        raise HTTPException(400, "At least 1 rating required")
    for r in payload.ratings:
        if r.ideal_temp_zone not in TEMP_ZONES:
            raise HTTPException(400, f"unknown temp zone: {r.ideal_temp_zone}")

    # Save each rating
    for r in payload.ratings:
        outfit = db.get(models.Outfit, r.outfit_id)
        if outfit is None:
            continue
        crud.upsert_rating(
            db, user_id=user.id, outfit_id=r.outfit_id,
            rating=r.rating, source=models.RatingSourceEnum.user_rated,
        )
        # Save the temp zone separately (upsert_rating doesn't know about it)
        existing = (
            db.query(models.Rating)
            .filter_by(user_id=user.id, outfit_id=r.outfit_id)
            .first()
        )
        if existing:
            existing.ideal_temp_zone = r.ideal_temp_zone
        crud.update_item_stats_for_outfit(db, r.outfit_id)
    db.commit()

    # Recompute temp_offset from all ratings with ideal_temp_zone
    rated = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user.id,
                models.Rating.ideal_temp_zone.isnot(None))
        .all()
    )
    deltas = []
    for r in rated:
        outfit = db.get(models.Outfit, r.outfit_id)
        if not outfit or outfit.context_id is None:
            continue
        ctx = db.get(models.DailyContext, outfit.context_id)
        if not ctx or ctx.temperature is None:
            continue
        user_ideal = ZONE_TO_TEMP[r.ideal_temp_zone]
        # Positive delta = user wears this outfit at warmer temps than baseline
        # → user is more cold-sensitive (needs more layers at given temp)
        deltas.append(user_ideal - ctx.temperature)
    if deltas:
        user.temp_offset = round(sum(deltas) / len(deltas), 2)

    # Mark complete?
    progress_obj = _progress(db, user.id)
    if progress_obj.batches_done >= REQUIRED_BATCHES and not user.training_complete:
        user.training_complete = True
    db.commit()

    # Retrain XGBoost (best-effort — don't fail the request if training fails)
    train_result = None
    try:
        from ..services.model_training import train_model
        train_result = train_model(db, user.id)
    except Exception as e:
        train_result = {"status": "error", "message": str(e)}

    return {
        "ok": True,
        "saved": len(payload.ratings),
        "temp_offset": user.temp_offset,
        "training_complete": user.training_complete,
        "progress": progress_obj.model_dump(),
        "train_result": train_result,
    }

"""Multi-stage training (Phase 4) — three independent sub-tracks:

  Stage 1 — temperature: which temp zones does this outfit fit?
  Stage 2 — aesthetic:   how good does it LOOK (independent of temp/occasion)?
  Stage 3 — occasion:    which events would you wear it to?

Order matters: temperature first ensures a clean signal before aesthetic
(so user doesn't down-rate "white tee + jeans + tuxedo coat at 32°C" because
of the coat being hot — they only judge temperature here). Aesthetic second
on temp-passing candidates removes the temperature confound. Occasion last,
on temp+aesthetic-passing candidates, removes both.
"""
from datetime import datetime
import json
import random
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from .. import crud, models, schemas
from ..database import get_db
from ..services.outfit_generator import (
    generate_candidates, persist_candidates,
    _compute_curves, _warmth_score, _layer_counts,
    _ideal_warmth_for_temp,
)
from ..services.multi_stage_training import (
    train_temperature_model, train_occasion_model,
)
from ..services.model_training import train_model as train_aesthetic_model


router = APIRouter(prefix="/training/v2", tags=["training_v2"])


# ─── Constants ───────────────────────────────────────────────────────────
MIN_PER_ZONE = 15           # stage 1: ratings per zone before considered covered
BATCH_SIZE = 5              # how many outfits per batch
WEIRD_RATIO = 0.8           # stage 1 sampling: 80% deliberately random combos
MIN_AESTHETIC = 50          # stage 2: total ratings before considered done
MIN_OCCASION = 60           # stage 3: total ratings before considered done

# Two-tier "pending item" priority:
#   tier "fresh"      = brand-new item (cov == 0), never appeared in any
#                       training/recommend outfit yet. We need rating signal
#                       on it RIGHT NOW so the model learns its features.
#                       → force-include in 100% of batch outfits.
#   tier "under_trained" = used (cov ≥ 1) but still below threshold. Mix
#                          with random so user doesn't get bored.
#                       → force-include in ~40% of batch outfits.
NEW_ITEM_RATIO_UNDER_TRAINED = 0.4
NEW_ITEM_COVERAGE_THRESHOLD = 5


def _pending_new_item(db: Session, user_id: int) -> Optional[Tuple[models.Item, str]]:
    """Pick the highest-priority "needs training" item, with the tier label.

    Strategy:
      1. Brand-new (cov == 0) — most recently uploaded first (id DESC).
         Just-uploaded items dominate the very next training session so the
         user can see their new clothes immediately rather than waiting for
         the FIFO queue to drain.
      2. Under-trained (cov < threshold) — oldest first (id ASC). Once the
         brand-new queue is clear we drain the long-tail in upload order.
      3. None — wardrobe is fully trained, normal random generation."""
    items = (
        db.query(models.Item)
        .outerjoin(models.ItemStats, models.ItemStats.item_id == models.Item.id)
        .filter(
            models.Item.user_id == user_id,
            models.Item.is_active == True,
        )
        .all()
    )
    # Pass 1: fresh items, most-recent first
    fresh = [it for it in items if (it.stats.coverage_count if it.stats else 0) == 0]
    if fresh:
        fresh.sort(key=lambda i: i.id, reverse=True)
        return (fresh[0], "fresh")
    # Pass 2: under-trained, oldest first
    under = [it for it in items
             if 0 < (it.stats.coverage_count if it.stats else 0) < NEW_ITEM_COVERAGE_THRESHOLD]
    if under:
        under.sort(key=lambda i: i.id)
        return (under[0], "under_trained")
    return None


def has_fresh_upload(db: Session, user_id: int) -> bool:
    """Quick check: is there any cov==0 item the user hasn't trained on yet?
    Used by the temperature-stage handler to disable weird-combo mode and
    focus the entire batch on the new upload."""
    p = _pending_new_item(db, user_id)
    return p is not None and p[1] == "fresh"


def _generate_with_pending(
    db: Session,
    ctx: "models.DailyContext",
    user_id: int,
    n: int,
) -> list:
    """Generate candidate outfits, biased to include a pending new item.

    For brand-new items (cov==0): EVERY candidate features the item, so the
    user sees it 5/5 times in the next batch and can rate it across
    different combos in one sitting.

    For under-trained items: 40% of the batch features the item, the rest
    is random — keeps variety while still pushing the long-tail."""
    pending = _pending_new_item(db, user_id)
    if pending is None:
        return generate_candidates(db, ctx, user_id, n=n)
    item, tier = pending
    if tier == "fresh":
        # 100% force-include — over-generate then dedupe so we still get
        # combinatorial variety in the OTHER slots (different bottoms,
        # shoes, etc.) while pinning this one item.
        cands = generate_candidates(
            db, ctx, user_id,
            n=n * 4,
            must_include_item_id=item.id,
        )
        seen = set()
        out = []
        for c in cands:
            sig = tuple(sorted(i.id for i in c["items"]))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(c)
            if len(out) >= n:
                break
        return out
    # under_trained: 40/60 mix with random
    n_forced = max(1, int(n * NEW_ITEM_RATIO_UNDER_TRAINED))
    n_random = max(0, n - n_forced)
    forced = generate_candidates(
        db, ctx, user_id,
        n=n_forced * 4,
        must_include_item_id=item.id,
    )
    rest = generate_candidates(db, ctx, user_id, n=n_random * 2) if n_random else []
    seen = set()
    combined = []
    for batch in (forced[:n_forced], rest):
        for c in batch:
            sig = tuple(sorted(i.id for i in c["items"]))
            if sig in seen:
                continue
            seen.add(sig)
            combined.append(c)
    return combined


def _temp_to_zone(t: float) -> str:
    if t < 0:  return "subzero"
    if t < 10: return "cold"
    if t < 18: return "cool"
    if t < 25: return "mild"
    if t < 30: return "warm"
    return "hot"


# ═════════════════════════════════════════════════════════════════════════
# STAGE 1 — Temperature
# ═════════════════════════════════════════════════════════════════════════

class TempZonesSubmit(BaseModel):
    outfit_id: int
    zones_ok: List[str]  # subset of TEMP_ZONE_KEYS, may be empty


class TempBatchSubmit(BaseModel):
    ratings: List[TempZonesSubmit]


def _zone_stats(db: Session, user_id: int) -> dict:
    """Per-zone training stats: total ratings & target_zone breakdown."""
    rows = (
        db.query(models.TemperatureRating)
        .filter(models.TemperatureRating.user_id == user_id)
        .all()
    )
    stats = {z: {
        "total": 0,
        "accepted_for_zone": 0,
        "warmth_accepted": [],
        "warmth_rejected": [],
    } for z in models.TEMP_ZONE_KEYS}
    for r in rows:
        # Count this rating against its target_zone (the zone we sampled it for)
        tz = r.target_zone or "mild"
        if tz in stats:
            stats[tz]["total"] += 1
            outfit = db.get(models.Outfit, r.outfit_id)
            warmth = float(outfit.warmth_score or 0) if outfit else 0
            if tz in (r.zones_ok or []):
                stats[tz]["accepted_for_zone"] += 1
                stats[tz]["warmth_accepted"].append(warmth)
            else:
                stats[tz]["warmth_rejected"].append(warmth)
    return stats


def _pick_target_zone(stats: dict) -> Optional[str]:
    """Adaptive zone picker:
    1. Prefer zones with total < MIN_PER_ZONE (coverage gap)
    2. Then zones with low acceptance rate (drift detection)
    3. Else None — stage 1 complete.
    """
    # Coverage gap
    gaps = [z for z in models.TEMP_ZONE_KEYS if stats[z]["total"] < MIN_PER_ZONE]
    if gaps:
        return random.choice(gaps)

    # Drift detection — zones where < 30% accepted
    drift = []
    for z in models.TEMP_ZONE_KEYS:
        s = stats[z]
        if s["total"] >= MIN_PER_ZONE:
            rate = s["accepted_for_zone"] / max(1, s["total"])
            if rate < 0.30:
                drift.append(z)
    if drift:
        return random.choice(drift)

    return None  # all zones covered with healthy acceptance


def _generate_weird_combo(
    db: Session, user_id: int, target_zone: str, target_warmth: Optional[float] = None,
) -> Optional[dict]:
    """Generate a deliberately random outfit (no aesthetic / occasion filter).
    Used by stage 1 to expose user to a wide variety of temperature combos.
    Returns a dict ready for persist_candidates()."""
    items = (
        db.query(models.Item)
        .filter(models.Item.user_id == user_id, models.Item.is_active == True)
        .all()
    )
    items = [i for i in items if not i.state or i.state.state == models.ItemStateEnum.clean]
    tops = [i for i in items if i.category == models.CategoryEnum.top]
    bottoms = [i for i in items if i.category == models.CategoryEnum.bottom]
    shoes = [i for i in items if i.category == models.CategoryEnum.shoes]
    fullbodies = [i for i in items if i.category == models.CategoryEnum.fullbody]

    if not shoes or not (tops or fullbodies) or not (bottoms or fullbodies):
        return None

    target_t = models.TEMP_ZONE_REPRESENTATIVE[target_zone]
    target_w = target_warmth if target_warmth is not None else _ideal_warmth_for_temp(target_t)

    # Try up to 30 random combos to land near target warmth (±25%)
    for _ in range(30):
        use_fullbody = bool(fullbodies) and random.random() < 0.15
        if use_fullbody:
            chosen = [random.choice(fullbodies), random.choice(shoes)]
        else:
            chosen_shoes = random.choice(shoes)
            chosen_bottom = random.choice(bottoms or fullbodies)
            n_tops = random.choices([1, 2, 3], weights=[6, 3, 1])[0]
            n_tops = min(n_tops, len(tops))
            chosen_tops = random.sample(tops, n_tops) if tops else []
            # ─── Outer-alone rule (respects item.can_wear_alone) ───────────
            # If the only tops are outer-role AND none of them flag
            # can_wear_alone=True, force-add a random inner/mid underneath.
            # Items with can_wear_alone=True (e.g. bralette, kimono) are
            # allowed solo for users whose closet has them.
            if chosen_tops and all(t.layer_role == models.LayerRoleEnum.outer for t in chosen_tops):
                if not any(t.can_wear_alone for t in chosen_tops):
                    inner_pool = [t for t in tops
                                  if t.layer_role in (models.LayerRoleEnum.inner, models.LayerRoleEnum.mid)
                                  and t.id not in {x.id for x in chosen_tops}]
                    if inner_pool:
                        chosen_tops.insert(0, random.choice(inner_pool))
            chosen = chosen_tops + [chosen_bottom, chosen_shoes]
            # 30% chance to add an accessory
            accessories = [i for i in items if i.category == models.CategoryEnum.accessory]
            if accessories and random.random() < 0.3:
                chosen.append(random.choice(accessories))

        warmth = _warmth_score(chosen)
        # Accept if within 50% of target
        if 0.5 * target_w <= warmth <= 1.5 * target_w:
            break

    # Compute curves (reuse outfit_generator helpers)
    layered = [i for i in chosen if i.category == models.CategoryEnum.top]
    bottoms_in = [i for i in chosen if i.category == models.CategoryEnum.bottom]
    fullbodies_in = [i for i in chosen if i.category == models.CategoryEnum.fullbody]
    curves = _compute_curves(layered, bottoms_in, fullbodies_in)
    counts = _layer_counts(layered)

    return {
        "items": chosen,
        "warmth_score": warmth,
        "coverage_curve": curves["coverage_curve"],
        "aesthetic_curve": curves["aesthetic_curve"],
        "t_cover": curves["t_cover"],
        "optimal_layer_count": None,  # not enforced for stage 1
        "final_k": None,
        "overkill_layers": 0,
        "underfit_flag": False,
        "aesthetic_stop_layers": [],
        "inner_count": counts["inner"],
        "mid_count": counts["mid"],
        "outer_count": counts["outer"],
        "total_items": len(chosen),
    }


def _make_synthetic_context(db: Session, user_id: int, target_zone: str) -> models.DailyContext:
    """Synthetic context for stage 1/2/3 outfits — temp matches target zone,
    occasion=casual (won't be used for filtering).

    The DB has UNIQUE(user_id, date), so we use microsecond resolution +
    a random offset to make every call's datetime distinct, even if the
    user races multiple training tabs.
    """
    t = models.TEMP_ZONE_REPRESENTATIVE[target_zone]
    distinct = datetime.utcnow().replace(microsecond=random.randint(0, 999_999))
    ctx = models.DailyContext(
        user_id=user_id,
        date=distinct,
        temperature=t,
        temperature_high=t + 2,
        temperature_low=t - 2,
        weather=models.WeatherEnum.cloudy,
        occasion=models.OccasionEnum.casual,
        notes=f"[v2 training] {target_zone} ({t}°C)",
    )
    db.add(ctx)
    db.commit()
    db.refresh(ctx)
    return ctx


@router.get("/temp/progress")
def temp_progress(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    stats = _zone_stats(db, user.id)
    total = sum(s["total"] for s in stats.values())
    # Per-zone summary
    zones_summary = []
    for z in models.TEMP_ZONE_KEYS:
        s = stats[z]
        rate = (s["accepted_for_zone"] / s["total"]) if s["total"] else 0.0
        zones_summary.append({
            "key": z,
            "range": models.TEMP_ZONE_RANGES[z],
            "total": s["total"],
            "accepted": s["accepted_for_zone"],
            "acceptance_rate": round(rate, 2),
            "covered": s["total"] >= MIN_PER_ZONE,
        })
    next_zone = _pick_target_zone(stats)
    return {
        "stage": "temperature",
        "total_ratings": total,
        "min_per_zone": MIN_PER_ZONE,
        "all_zones_covered": next_zone is None,
        "next_target_zone": next_zone,
        "zones": zones_summary,
        "completed": user.v2_temp_done,
    }


@router.get("/temp/next")
def temp_next(db: Session = Depends(get_db)):
    """Generate the next batch of stage 1 outfits using adaptive zone selection."""
    user = crud.get_or_create_default_user(db)
    stats = _zone_stats(db, user.id)
    target_zone = _pick_target_zone(stats)
    if target_zone is None:
        return {"done": True, "outfits": []}

    # Adaptive warmth target — if zone has low acceptance, push warmth toward
    # whatever the user has accepted previously.
    target_warmth = None
    s = stats[target_zone]
    if s["accepted_for_zone"] >= 3 and s["total"] >= MIN_PER_ZONE:
        rate = s["accepted_for_zone"] / s["total"]
        if rate < 0.30:
            # Concentrate near user's accepted-warmth mean
            target_warmth = sum(s["warmth_accepted"]) / len(s["warmth_accepted"])

    ctx = _make_synthetic_context(db, user.id, target_zone)
    candidates = []
    # When the user just uploaded fresh items, override the weird/baseline
    # split — go 100% baseline so _generate_with_pending can force-include
    # the new item in every outfit. Otherwise the weird-combo path bypasses
    # pending logic and the user keeps not seeing what they just uploaded.
    if has_fresh_upload(db, user.id):
        n_weird = 0
        n_baseline = BATCH_SIZE
    else:
        # Normal: 80% weird, 20% generator baseline
        n_weird = int(BATCH_SIZE * WEIRD_RATIO)
        n_baseline = BATCH_SIZE - n_weird
    for _ in range(n_weird):
        c = _generate_weird_combo(db, user.id, target_zone, target_warmth)
        if c:
            candidates.append(c)
    if n_baseline:
        # Use _generate_with_pending so newly-uploaded items (coverage_count
        # below NEW_ITEM_COVERAGE_THRESHOLD) get force-included in the
        # baseline portion until the model has enough signal on them.
        baseline = _generate_with_pending(db, ctx, user.id, n=n_baseline * 4)
        random.shuffle(baseline)
        candidates.extend(baseline[:n_baseline])

    if not candidates:
        raise HTTPException(400, f"Could not generate stage 1 outfits for zone {target_zone}")

    saved = persist_candidates(db, candidates[:BATCH_SIZE], ctx.id, user.id)

    return {
        "done": False,
        "target_zone": target_zone,
        "target_zone_range": models.TEMP_ZONE_RANGES[target_zone],
        "outfits": [_outfit_summary(o) for o in saved],
        "context_id": ctx.id,
    }


@router.post("/temp/submit")
def temp_submit(payload: TempBatchSubmit, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    valid_zones = set(models.TEMP_ZONE_KEYS)
    submitted = 0
    for r in payload.ratings:
        if not isinstance(r.zones_ok, list):
            continue
        zones = [z for z in r.zones_ok if z in valid_zones]
        # Determine target_zone from outfit context
        outfit = db.get(models.Outfit, r.outfit_id)
        target_zone = "mild"
        if outfit and outfit.context_id:
            ctx = db.get(models.DailyContext, outfit.context_id)
            if ctx and ctx.temperature is not None:
                target_zone = _temp_to_zone(ctx.temperature)
        # Upsert
        existing = (
            db.query(models.TemperatureRating)
            .filter_by(user_id=user.id, outfit_id=r.outfit_id)
            .first()
        )
        if existing:
            existing.zones_ok = zones
            existing.target_zone = target_zone
        else:
            db.add(models.TemperatureRating(
                user_id=user.id,
                outfit_id=r.outfit_id,
                zones_ok=zones,
                target_zone=target_zone,
            ))
        # Bump coverage_count so brand-new items graduate out of the
        # "fresh" tier after a batch (else they'd dominate forever).
        crud.increment_coverage_for_outfit(db, r.outfit_id)
        submitted += 1
    db.commit()

    # Check completion
    stats = _zone_stats(db, user.id)
    next_zone = _pick_target_zone(stats)
    if next_zone is None and not user.v2_temp_done:
        user.v2_temp_done = True
        db.commit()

    # Background-style retrain on every batch (XGBoost is fast enough)
    train_result = train_temperature_model(db, user.id)
    return {
        "submitted": submitted,
        "stage1_done": user.v2_temp_done,
        "train": train_result,
    }


# ═════════════════════════════════════════════════════════════════════════
# STAGE 2 — Aesthetic
# ═════════════════════════════════════════════════════════════════════════

class AestheticSubmit(BaseModel):
    outfit_id: int
    rating: int  # -1, 0, 1, 2


class AestheticBatchSubmit(BaseModel):
    ratings: List[AestheticSubmit]


@router.get("/aesthetic/progress")
def aesthetic_progress(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    n = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user.id)
        .count()
    )
    return {
        "stage": "aesthetic",
        "total_ratings": n,
        "min_required": MIN_AESTHETIC,
        "completed": user.v2_aesthetic_done,
        "stage1_done": user.v2_temp_done,
    }


@router.get("/aesthetic/next")
def aesthetic_next(db: Session = Depends(get_db)):
    """Stage 2 batch — uses generator's regular cohesive candidates.
    Tries multiple temp zones (in random order) until one yields enough
    candidates, so a wardrobe gap in one zone doesn't break the page."""
    user = crud.get_or_create_default_user(db)
    zones_to_try = list(models.TEMP_ZONE_KEYS[1:5])  # cold..warm
    random.shuffle(zones_to_try)
    last_ctx = None
    for target_zone in zones_to_try:
        ctx = _make_synthetic_context(db, user.id, target_zone)
        last_ctx = ctx
        cands = _generate_with_pending(db, ctx, user.id, n=BATCH_SIZE * 3)
        if cands:
            random.shuffle(cands)
            saved = persist_candidates(db, cands[:BATCH_SIZE], ctx.id, user.id)
            return {
                "outfits": [_outfit_summary(o) for o in saved],
                "context_id": ctx.id,
            }
    raise HTTPException(400, "Could not generate stage 2 outfits in any temp zone — wardrobe may be too small")


@router.post("/aesthetic/submit")
def aesthetic_submit(payload: AestheticBatchSubmit, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    submitted = 0
    for r in payload.ratings:
        if r.rating not in (-1, 0, 1, 2):
            continue
        crud.upsert_rating(
            db, user_id=user.id, outfit_id=r.outfit_id,
            rating=r.rating, source=models.RatingSourceEnum.user_rated,
        )
        # Increment coverage_count on items in this rated outfit so a
        # brand-new item (cov=0) graduates out of the "fresh" tier after
        # one training batch instead of dominating every subsequent batch.
        crud.increment_coverage_for_outfit(db, r.outfit_id)
        submitted += 1
    n = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user.id)
        .count()
    )
    if n >= MIN_AESTHETIC and not user.v2_aesthetic_done:
        user.v2_aesthetic_done = True
        db.commit()
    train_result = train_aesthetic_model(db, user.id)
    return {
        "submitted": submitted, "total": n,
        "stage2_done": user.v2_aesthetic_done,
        "train": train_result,
    }


# ═════════════════════════════════════════════════════════════════════════
# STAGE 3 — Occasion
# ═════════════════════════════════════════════════════════════════════════

class OccasionSubmit(BaseModel):
    outfit_id: int
    events_ok: List[str]


class OccasionBatchSubmit(BaseModel):
    ratings: List[OccasionSubmit]


@router.get("/occasion/progress")
def occasion_progress(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    n = (
        db.query(models.OccasionRating)
        .filter(models.OccasionRating.user_id == user.id)
        .count()
    )
    return {
        "stage": "occasion",
        "total_ratings": n,
        "min_required": MIN_OCCASION,
        "events": [{"key": e, "formality": models.EVENT_FORMALITY[e]} for e in models.EVENT_KEYS],
        "completed": user.v2_occasion_done,
        "stage2_done": user.v2_aesthetic_done,
    }


@router.get("/occasion/next")
def occasion_next(db: Session = Depends(get_db)):
    """Stage 3 batch — tries multiple temp zones in random order."""
    user = crud.get_or_create_default_user(db)
    zones_to_try = list(models.TEMP_ZONE_KEYS[1:5])
    random.shuffle(zones_to_try)
    for target_zone in zones_to_try:
        ctx = _make_synthetic_context(db, user.id, target_zone)
        cands = _generate_with_pending(db, ctx, user.id, n=BATCH_SIZE * 3)
        if cands:
            random.shuffle(cands)
            saved = persist_candidates(db, cands[:BATCH_SIZE], ctx.id, user.id)
            return {
                "outfits": [_outfit_summary(o) for o in saved],
                "events": models.EVENT_KEYS,
            }
    raise HTTPException(400, "Could not generate stage 3 outfits in any temp zone")


@router.post("/occasion/submit")
def occasion_submit(payload: OccasionBatchSubmit, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    valid_events = set(models.EVENT_KEYS)
    submitted = 0
    for r in payload.ratings:
        events = [e for e in (r.events_ok or []) if e in valid_events]
        existing = (
            db.query(models.OccasionRating)
            .filter_by(user_id=user.id, outfit_id=r.outfit_id)
            .first()
        )
        if existing:
            existing.events_ok = events
        else:
            db.add(models.OccasionRating(
                user_id=user.id, outfit_id=r.outfit_id, events_ok=events,
            ))
        # Bump cov so brand-new items graduate after one batch.
        crud.increment_coverage_for_outfit(db, r.outfit_id)
        submitted += 1
    db.commit()
    n = (
        db.query(models.OccasionRating)
        .filter(models.OccasionRating.user_id == user.id)
        .count()
    )
    if n >= MIN_OCCASION and not user.v2_occasion_done:
        user.v2_occasion_done = True
        db.commit()
    train_result = train_occasion_model(db, user.id)
    return {
        "submitted": submitted, "total": n,
        "stage3_done": user.v2_occasion_done,
        "train": train_result,
    }


# ─── Shared helpers ─────────────────────────────────────────────────────
def _outfit_summary(o: models.Outfit) -> dict:
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
        "warmth_score": float(o.warmth_score or 0),
        "coverage_curve": o.coverage_curve or [],
        "aesthetic_curve": o.aesthetic_curve or [],
    }

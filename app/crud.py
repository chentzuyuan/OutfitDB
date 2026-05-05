from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from . import models


DEFAULT_USER_ID = 1


def get_or_create_default_user(db: Session) -> models.User:
    user = db.get(models.User, DEFAULT_USER_ID)
    if user is None:
        user = models.User(id=DEFAULT_USER_ID, username="default")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def list_items(
    db: Session,
    user_id: int = DEFAULT_USER_ID,
    category: Optional[str] = None,
    state: Optional[str] = None,
    active_only: bool = True,
) -> List[models.Item]:
    q = db.query(models.Item).filter(models.Item.user_id == user_id)
    if active_only:
        q = q.filter(models.Item.is_active == True)
    if category:
        q = q.filter(models.Item.category == category)
    items = q.order_by(models.Item.id.desc()).all()
    if state:
        items = [i for i in items if i.state and i.state.state == state]
    return items


def get_item(db: Session, item_id: int) -> Optional[models.Item]:
    return db.get(models.Item, item_id)


def _normalize_composition_payload(data: dict) -> None:
    """Normalize composition + auto-derive primary `material`.

    Mutates `data` in place. Accepts both Pydantic CompositionEntry instances
    (from Pydantic validation) and plain dicts (from internal callers).
    If composition is set: `material` is forced to the highest-pct entry's
    material, so legacy filters/queries on Item.material still match the
    blend's dominant component."""
    comp = data.get("composition")
    if not comp:
        # Drop empty/None to keep it consistent (None means "100% of material")
        data["composition"] = None
        return
    # Coerce Pydantic models → plain dicts for JSON storage
    coerced = []
    for c in comp:
        if hasattr(c, "model_dump"):
            coerced.append(c.model_dump())
        else:
            coerced.append({"material": str(c["material"]).lower(),
                            "pct": int(c["pct"])})
    data["composition"] = coerced
    primary = max(coerced, key=lambda c: c["pct"])
    data["material"] = primary["material"]


def create_item(db: Session, data: dict, user_id: int = DEFAULT_USER_ID) -> models.Item:
    _normalize_composition_payload(data)
    # Smart default for wears_per_wash if caller didn't specify
    if "wears_per_wash" not in data or data.get("wears_per_wash") in (None, 0):
        cat = (data.get("category") or "")
        cat = cat.value if hasattr(cat, "value") else str(cat)
        mat = data.get("material") or ""
        data["wears_per_wash"] = default_wears_per_wash(cat, mat)
    item = models.Item(user_id=user_id, **data)
    db.add(item)
    db.flush()
    db.add(models.ItemState(item_id=item.id))
    db.add(models.ItemStats(item_id=item.id))
    db.commit()
    db.refresh(item)
    return item


def update_item_stats_for_outfit(db: Session, outfit_id: int):
    outfit = db.get(models.Outfit, outfit_id)
    if not outfit:
        return
    for oi in outfit.outfit_items:
        _recompute_item_stats(db, oi.item_id)
    db.commit()


def _recompute_item_stats(db: Session, item_id: int):
    stats = db.query(models.ItemStats).filter_by(item_id=item_id).first()
    if stats is None:
        stats = models.ItemStats(item_id=item_id)
        db.add(stats)
        db.flush()

    ratings = (
        db.query(models.Rating.rating)
        .join(models.Outfit, models.Rating.outfit_id == models.Outfit.id)
        .join(models.OutfitItem, models.OutfitItem.outfit_id == models.Outfit.id)
        .filter(models.OutfitItem.item_id == item_id)
        .all()
    )
    if ratings:
        vals = [r[0] for r in ratings]
        stats.total_ratings = len(vals)
        stats.average_rating = sum(vals) / len(vals)
    else:
        stats.total_ratings = 0
        stats.average_rating = None


def increment_coverage_for_outfit(db: Session, outfit_id: int):
    outfit = db.get(models.Outfit, outfit_id)
    if not outfit:
        return
    for oi in outfit.outfit_items:
        stats = db.query(models.ItemStats).filter_by(item_id=oi.item_id).first()
        if stats is None:
            stats = models.ItemStats(item_id=oi.item_id)
            db.add(stats)
            db.flush()
        stats.coverage_count = (stats.coverage_count or 0) + 1
    db.commit()


def upsert_rating(
    db: Session,
    user_id: int,
    outfit_id: int,
    rating: int,
    source: models.RatingSourceEnum = models.RatingSourceEnum.user_rated,
) -> models.Rating:
    existing = (
        db.query(models.Rating)
        .filter_by(user_id=user_id, outfit_id=outfit_id)
        .first()
    )
    if existing:
        existing.rating = rating
        existing.rating_source = source
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    r = models.Rating(
        user_id=user_id,
        outfit_id=outfit_id,
        rating=rating,
        rating_source=source,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def upsert_context(db: Session, user_id: int, data: dict) -> models.DailyContext:
    date_val = data["date"]
    existing = (
        db.query(models.DailyContext)
        .filter_by(user_id=user_id, date=date_val)
        .first()
    )
    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return existing
    ctx = models.DailyContext(user_id=user_id, **data)
    db.add(ctx)
    db.commit()
    db.refresh(ctx)
    return ctx


def default_wears_per_wash(category: str, material: str) -> int:
    """Sensible default for an item's wears-per-wash based on category + material.
    User can override per-item. Lower = washed more often.
    """
    cat = (category or "").lower()
    mat = (material or "").lower()
    # Athletic / inner pieces — wash every wear
    # (only relevant if user adds underwear / socks; we don't have those in
    # the wardrobe spec, but the rule is here for completeness)
    if mat in ("polyester",) and cat == "bottom":
        # athletic shorts / basketball shorts → soaked in sweat
        return 1
    if cat == "shoes":
        return 30  # shoes don't really get "washed" between wears
    if cat == "accessory":
        return 20  # belts / hats / scarves rarely need washing
    if cat == "bottom" and mat == "denim":
        return 5   # jeans tradition — wash less
    if cat == "bottom" and mat == "wool":
        return 5   # wool trousers — dry-clean infrequently
    if cat == "top":
        # outerwear / heavy knits — wash way less than t-shirts
        if mat in ("wool", "cashmere", "leather", "nylon"):
            return 8
        if mat == "linen":
            return 2
        return 3   # default for cotton tees, polos, shirts
    if cat == "fullbody":
        return 3
    return 3


def mark_outfit_worn(db: Session, outfit_id: int):
    """After the user accepts/modifies an outfit, increment wear counts and
    auto-flip cleanliness state:
        wear_count == 0                          → clean
        0 < wear_count < wears_per_wash          → worn   (still wearable)
        wear_count >= wears_per_wash             → in_laundry (auto-blocked)
    """
    outfit = db.get(models.Outfit, outfit_id)
    if not outfit:
        return
    now = datetime.utcnow()
    for oi in outfit.outfit_items:
        item = oi.item or db.get(models.Item, oi.item_id)
        if not item:
            continue
        st = db.query(models.ItemState).filter_by(item_id=oi.item_id).first()
        if st is None:
            st = models.ItemState(item_id=oi.item_id)
            db.add(st)
        st.last_worn = now
        st.worn_count = (st.worn_count or 0) + 1
        st.wear_count_since_wash = (st.wear_count_since_wash or 0) + 1

        # Auto-flip state — never override `unavailable` (manually marked broken/lost)
        if st.state != models.ItemStateEnum.unavailable:
            wpw = max(1, int(item.wears_per_wash or 3))
            if st.wear_count_since_wash >= wpw:
                st.state = models.ItemStateEnum.in_laundry
            else:
                st.state = models.ItemStateEnum.worn

        stats = db.query(models.ItemStats).filter_by(item_id=oi.item_id).first()
        if stats:
            stats.last_worn_date = now
    db.commit()


def item_to_out(item: models.Item) -> dict:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "name": item.name,
        "category": item.category,
        "layer_role": item.layer_role,
        "colors": item.colors or [],
        "is_multicolor": item.is_multicolor,
        "pattern": item.pattern,
        "pattern_complexity": item.pattern_complexity,
        "material": item.material,
        "composition": item.composition,
        "thickness": item.thickness,
        "style_tags": item.style_tags or [],
        "can_wear_alone": item.can_wear_alone,
        "shoulder_fit": item.shoulder_fit,
        "waist_fit": item.waist_fit,
        "collar": item.collar,
        "top_length": item.top_length,
        "sleeve": item.sleeve,
        "pants_length": item.pants_length,
        "pants_fit": item.pants_fit,
        "image_path": item.image_path,
        "is_active": item.is_active,
        "created_at": item.created_at,
        "state": item.state.state if item.state else None,
        "coverage_count": item.stats.coverage_count if item.stats else 0,
        # Real wear count — incremented when the user logs an outfit as worn.
        # Distinct from `coverage_count` which counts top-K appearances during
        # recommendation/training. Surface this in UI; coverage_count is internal.
        "worn_count": int(item.state.worn_count or 0) if item.state else 0,
        "average_rating": item.stats.average_rating if item.stats else None,
        # Laundry / cleanliness exposure
        "wears_per_wash": int(item.wears_per_wash or 3),
        "wear_count_since_wash": int(item.state.wear_count_since_wash or 0) if item.state else 0,
    }

import json
import time
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session
from PIL import Image

from .. import crud, models, schemas
from ..database import get_db
from ..config import get_data_dir


router = APIRouter(prefix="/items", tags=["items"])

LEGACY_UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "static" / "uploads"


def _get_upload_root() -> Path:
    """Local-first: write images to data_dir/items/images/. Fallback to legacy path before setup."""
    data_dir = get_data_dir()
    if data_dir is not None:
        root = data_dir / "items" / "images"
        root.mkdir(parents=True, exist_ok=True)
        return root
    return LEGACY_UPLOAD_ROOT


def _parse_json_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    raw = raw.strip()
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        pass
    # fallback: comma-separated
    return [s.strip() for s in raw.split(",") if s.strip()]


def _save_image(file: UploadFile, user_id: int, item_id: int) -> str:
    upload_root = _get_upload_root()
    ts = int(time.time())
    filename = f"{item_id}_{ts}.jpg"
    out_path = upload_root / filename
    try:
        img = Image.open(file.file)
        img = img.convert("RGB")
        img.thumbnail((800, 800))
        img.save(out_path, "JPEG", quality=85)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image processing failed: {e}")
    # local-first: data_dir/items/images/{filename} → URL /images/{filename}
    if get_data_dir() is not None:
        return f"/images/{filename}"
    # legacy fallback (pre-setup)
    return f"/static/uploads/{user_id}/{filename}"


def _infer_layer_role(category: str, thickness: str, can_wear_alone: bool) -> str:
    # top + fullbody share the same layering semantics
    if category not in ("top", "fullbody"):
        return "none"
    if thickness == "very_thin":
        return "inner"
    if thickness == "thin":
        return "mid" if can_wear_alone else "inner"
    if thickness == "very_thick":
        return "outer"
    return "outer" if can_wear_alone else "mid"


@router.post("/upload", response_model=schemas.ItemOut)
async def upload_item(
    name: str = Form(...),
    category: str = Form(...),
    colors: Optional[str] = Form(None),
    is_multicolor: bool = Form(False),
    pattern: str = Form("solid"),
    pattern_complexity: int = Form(0),
    material: str = Form("cotton"),
    composition: Optional[str] = Form(None),  # JSON-encoded list of {material, pct}
    thickness: str = Form("thin"),
    style_tags: Optional[str] = Form(None),
    can_wear_alone: bool = Form(True),
    shoulder_fit: Optional[str] = Form(None),
    waist_fit: Optional[str] = Form(None),
    collar: Optional[str] = Form(None),
    top_length: Optional[str] = Form(None),
    sleeve: Optional[str] = Form(None),
    pants_length: Optional[str] = Form(None),
    pants_fit: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    crud.get_or_create_default_user(db)
    layer_role = _infer_layer_role(category, thickness, can_wear_alone)
    # Parse + validate composition (sum to 100, no dupes) via the Pydantic schema
    parsed_composition = None
    if composition:
        try:
            raw = json.loads(composition)
            if isinstance(raw, list) and raw:
                entries = [schemas.CompositionEntry(**c) for c in raw]
                schemas._validate_composition(entries)
                parsed_composition = [e.model_dump() for e in entries]
        except (ValueError, TypeError) as e:
            raise HTTPException(400, f"invalid composition: {e}")
    data = {
        "name": name,
        "category": models.CategoryEnum(category),
        "layer_role": models.LayerRoleEnum(layer_role),
        "colors": _parse_json_list(colors),
        "is_multicolor": is_multicolor,
        "pattern": pattern,
        "pattern_complexity": pattern_complexity,
        "material": material,
        "composition": parsed_composition,  # crud will derive primary material
        "thickness": models.ThicknessEnum(thickness),
        "style_tags": _parse_json_list(style_tags),
        "can_wear_alone": can_wear_alone,
        "shoulder_fit": shoulder_fit,
        "waist_fit": waist_fit,
        "collar": collar,
        "top_length": top_length,
        "sleeve": sleeve,
        "pants_length": pants_length,
        "pants_fit": pants_fit,
    }
    item = crud.create_item(db, data)

    if image is not None and image.filename:
        path = _save_image(image, item.user_id, item.id)
        item.image_path = path
        db.commit()
        db.refresh(item)
    return crud.item_to_out(item)


@router.post("/{item_id}/image", response_model=schemas.ItemOut)
async def replace_item_image(
    item_id: int,
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Add/replace the image of an existing item.

    Used by:
      • Closet right-click "Update image" — useful for items that were
        bulk-imported via CSV (no image yet) or whose original photo
        turned out blurry / wrong angle.
      • Anywhere else that wants to swap an item's photo without
        re-uploading all its metadata.

    The old image file (if any) is overwritten via _save_image, which
    uses a deterministic filename pattern; the DB image_path is updated
    to the new URL so the closet UI picks it up on next refresh."""
    item = crud.get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    if not image or not image.filename:
        raise HTTPException(status_code=400, detail="no image uploaded")
    path = _save_image(image, item.user_id, item.id)
    item.image_path = path
    db.commit()
    db.refresh(item)
    return crud.item_to_out(item)


@router.get("/", response_model=List[schemas.ItemOut])
def list_items(
    category: Optional[str] = None,
    state: Optional[str] = None,
    color: Optional[str] = None,         # filter by color membership in items.colors
    material: Optional[str] = None,      # filter by exact material match
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    items = crud.list_items(db, category=category, state=state, active_only=active_only)
    if color:
        c = color.lower()
        items = [i for i in items if c in [x.lower() for x in (i.colors or [])]]
    if material:
        m = material.lower()
        def _matches_material(it):
            # Match the primary material OR any blend component, so a
            # "cotton" filter surfaces both pure-cotton items and 40/60
            # cotton-linen blends.
            if (it.material or "").lower() == m:
                return True
            for c in (it.composition or []):
                if isinstance(c, dict) and (c.get("material") or "").lower() == m:
                    return True
            return False
        items = [i for i in items if _matches_material(i)]
    return [crud.item_to_out(i) for i in items]


@router.patch("/{item_id}/state", response_model=schemas.ItemOut)
def patch_state(item_id: int, patch: schemas.ItemStatePatch, db: Session = Depends(get_db)):
    item = crud.get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    if item.state is None:
        item.state = models.ItemState(item_id=item.id)
    item.state.state = patch.state
    # If user explicitly sets clean, also reset wear count
    if patch.state == models.ItemStateEnum.clean:
        item.state.wear_count_since_wash = 0
    db.commit()
    db.refresh(item)
    return crud.item_to_out(item)


@router.patch("/{item_id}/cleanliness", response_model=schemas.ItemOut)
def patch_cleanliness(
    item_id: int,
    patch: schemas.ItemCleanlinessPatch,
    db: Session = Depends(get_db),
):
    """Manually adjust an item's cleanliness — any combination of:
       - wear_count_since_wash (set absolute count)
       - state (override; auto-recomputed if not given)
       - wears_per_wash (override per-item wash threshold)
    Whatever isn't explicitly set is left alone (or auto-recomputed)."""
    item = crud.get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    if item.state is None:
        item.state = models.ItemState(item_id=item.id)

    if patch.wears_per_wash is not None:
        if patch.wears_per_wash < 1:
            raise HTTPException(400, "wears_per_wash must be ≥ 1")
        item.wears_per_wash = patch.wears_per_wash

    if patch.wear_count_since_wash is not None:
        if patch.wear_count_since_wash < 0:
            raise HTTPException(400, "wear_count_since_wash must be ≥ 0")
        item.state.wear_count_since_wash = patch.wear_count_since_wash

    # Determine state: explicit takes precedence; otherwise recompute from counts
    if patch.state is not None:
        item.state.state = patch.state
        if patch.state == models.ItemStateEnum.clean:
            item.state.wear_count_since_wash = 0
    else:
        wpw = max(1, int(item.wears_per_wash or 3))
        n = int(item.state.wear_count_since_wash or 0)
        # Don't overwrite manually-set unavailable
        if item.state.state != models.ItemStateEnum.unavailable:
            if n == 0:
                item.state.state = models.ItemStateEnum.clean
            elif n >= wpw:
                item.state.state = models.ItemStateEnum.in_laundry
            else:
                item.state.state = models.ItemStateEnum.worn

    db.commit()
    db.refresh(item)
    return crud.item_to_out(item)


@router.patch("/{item_id}", response_model=schemas.ItemOut)
def update_item(
    item_id: int,
    patch: schemas.ItemUpdate,
    db: Session = Depends(get_db),
):
    """Generic item-attribute update — wears_per_wash, name, composition, material."""
    item = crud.get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    if patch.wears_per_wash is not None:
        if patch.wears_per_wash < 1:
            raise HTTPException(400, "wears_per_wash must be ≥ 1")
        item.wears_per_wash = patch.wears_per_wash
    if patch.name is not None and patch.name.strip():
        item.name = patch.name.strip()
    # Composition update — auto-derives primary material from blend.
    # Sending an empty list explicitly clears the blend (back to 100% material).
    if patch.composition is not None:
        if len(patch.composition) == 0:
            item.composition = None
        else:
            comp_payload = {"composition": [c.model_dump() for c in patch.composition]}
            crud._normalize_composition_payload(comp_payload)
            item.composition = comp_payload["composition"]
            item.material = comp_payload["material"]
    elif patch.material is not None and patch.material.strip():
        # Manual override of primary material when no blend was provided.
        item.material = patch.material.strip().lower()
    db.commit()
    db.refresh(item)
    return crud.item_to_out(item)


@router.post("/laundry", response_model=List[schemas.ItemOut])
def bulk_laundry(req: schemas.ItemBulkLaunderRequest, db: Session = Depends(get_db)):
    """Reset given items to clean state with wear_count_since_wash = 0.
    Used by /closet's Laundry mode batch action."""
    if not req.item_ids:
        raise HTTPException(400, "item_ids must be non-empty")
    out = []
    for iid in req.item_ids:
        item = crud.get_item(db, iid)
        if item is None:
            continue
        if item.state is None:
            item.state = models.ItemState(item_id=item.id)
        item.state.wear_count_since_wash = 0
        item.state.state = models.ItemStateEnum.clean
        out.append(item)
    db.commit()
    for it in out:
        db.refresh(it)
    return [crud.item_to_out(it) for it in out]


@router.post("/unavailable", response_model=List[schemas.ItemOut])
def bulk_mark_unavailable(req: schemas.ItemBulkLaunderRequest, db: Session = Depends(get_db)):
    """Bulk-flip items to `unavailable` (repair, seasonal storage, lent out).

    Differs from /items/dirty: this does NOT touch wear_count_since_wash —
    the item isn't dirty, just temporarily out of the rotation. Item stays
    invisible to the recommender until /items/available brings it back."""
    if not req.item_ids:
        raise HTTPException(400, "item_ids must be non-empty")
    out = []
    for iid in req.item_ids:
        item = crud.get_item(db, iid)
        if item is None:
            continue
        if item.state is None:
            item.state = models.ItemState(item_id=item.id)
        item.state.state = models.ItemStateEnum.unavailable
        out.append(item)
    db.commit()
    for it in out:
        db.refresh(it)
    return [crud.item_to_out(it) for it in out]


@router.post("/available", response_model=List[schemas.ItemOut])
def bulk_mark_available(req: schemas.ItemBulkLaunderRequest, db: Session = Depends(get_db)):
    """Bulk-release items from `unavailable` back to `clean`.

    Use case: brought back from repair, end of season, friend returned the
    sweater. We don't reset wear_count (the prior wear history is still
    valid context); use /items/laundry if you ALSO want to wash."""
    if not req.item_ids:
        raise HTTPException(400, "item_ids must be non-empty")
    out = []
    for iid in req.item_ids:
        item = crud.get_item(db, iid)
        if item is None:
            continue
        if item.state is None:
            item.state = models.ItemState(item_id=item.id)
        item.state.state = models.ItemStateEnum.clean
        out.append(item)
    db.commit()
    for it in out:
        db.refresh(it)
    return [crud.item_to_out(it) for it in out]


@router.post("/dirty", response_model=List[schemas.ItemOut])
def bulk_mark_dirty(req: schemas.ItemBulkLaunderRequest, db: Session = Depends(get_db)):
    """Bulk mark items as in_laundry without washing them.

    Use case: clothes are too dirty to wear today but the user doesn't
    have time to actually wash them — they want the recommender to stop
    suggesting these items until laundry day. Sets state=in_laundry and
    wear_count_since_wash=wears_per_wash for each (the count is benign:
    when the user later runs `/laundry`, it resets to 0 anyway)."""
    if not req.item_ids:
        raise HTTPException(400, "item_ids must be non-empty")
    out = []
    for iid in req.item_ids:
        item = crud.get_item(db, iid)
        if item is None:
            continue
        if item.state is None:
            item.state = models.ItemState(item_id=item.id)
        item.state.wear_count_since_wash = max(1, int(item.wears_per_wash or 3))
        item.state.state = models.ItemStateEnum.in_laundry
        out.append(item)
    db.commit()
    for it in out:
        db.refresh(it)
    return [crud.item_to_out(it) for it in out]


def _delete_image_file(image_path: Optional[str]) -> bool:
    """Try to remove the on-disk image. Idempotent — silent if missing."""
    if not image_path:
        return False
    try:
        if image_path.startswith("/images/"):
            data_dir = get_data_dir()
            if data_dir is None:
                return False
            p = data_dir / "items" / "images" / Path(image_path).name
        elif image_path.startswith("/static/uploads/"):
            rel = image_path[len("/static/"):]
            p = Path(__file__).resolve().parent.parent / "static" / rel
        else:
            return False
        if p.exists():
            p.unlink()
            return True
    except Exception:
        pass
    return False


@router.post("/import_csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Bulk import items from a CSV file. Required columns: name, category, thickness.
    Optional: material, pattern, colors (semicolon-separated), style_tags, can_wear_alone,
    is_multicolor, sleeve, collar, shoulder_fit, waist_fit, top_length, pants_fit, pants_length.
    Skips rows with missing required fields or invalid enum values.
    """
    import csv
    import io
    crud.get_or_create_default_user(db)
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    created = []
    errors = []
    for i, row in enumerate(reader, start=2):  # start=2: line 1 = header
        try:
            name = (row.get("name") or "").strip()
            cat = (row.get("category") or "").strip().lower()
            thick = (row.get("thickness") or "thin").strip().lower()
            if not name or not cat:
                errors.append({"line": i, "error": "missing name or category"})
                continue
            try:
                cat_enum = models.CategoryEnum(cat)
                thick_enum = models.ThicknessEnum(thick)
            except ValueError as e:
                errors.append({"line": i, "error": f"invalid enum: {e}"})
                continue

            def _parse_bool(v):
                return str(v or "").strip().lower() in ("1", "true", "yes", "y", "t")

            def _parse_list(v):
                return [s.strip() for s in str(v or "").replace(",", ";").split(";") if s.strip()]

            colors = _parse_list(row.get("colors"))
            style_tags = _parse_list(row.get("style_tags"))
            can_wear_alone = _parse_bool(row.get("can_wear_alone")) if row.get("can_wear_alone") else True
            is_multicolor = _parse_bool(row.get("is_multicolor"))
            layer_role = _infer_layer_role(cat, thick, can_wear_alone)

            data = {
                "name": name,
                "category": cat_enum,
                "layer_role": models.LayerRoleEnum(layer_role),
                "colors": colors,
                "is_multicolor": is_multicolor,
                "pattern": (row.get("pattern") or "solid").strip().lower(),
                "pattern_complexity": int(row.get("pattern_complexity") or 0),
                "material": (row.get("material") or "cotton").strip().lower(),
                "thickness": thick_enum,
                "style_tags": style_tags,
                "can_wear_alone": can_wear_alone,
                "shoulder_fit": (row.get("shoulder_fit") or "").strip() or None,
                "waist_fit": (row.get("waist_fit") or "").strip() or None,
                "collar": (row.get("collar") or "").strip() or None,
                "top_length": (row.get("top_length") or "").strip() or None,
                "sleeve": (row.get("sleeve") or "").strip() or None,
                "pants_length": (row.get("pants_length") or "").strip() or None,
                "pants_fit": (row.get("pants_fit") or "").strip() or None,
            }
            item = crud.create_item(db, data)
            created.append({"id": item.id, "name": item.name, "category": item.category.value})
        except Exception as e:
            errors.append({"line": i, "error": str(e)})

    return {"created": len(created), "errors": errors, "items": created}


@router.delete("/{item_id}")
def hard_delete(item_id: int, db: Session = Depends(get_db)):
    """Hard delete: remove item row, image file, and dependent outfit_items rows.
    Preserves: ratings, outfit_logs, outfits (training data stays intact).
    The orphaned outfits will simply have fewer items in feature engineering."""
    item = crud.get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    image_path = item.image_path

    # remove outfit_items referencing this item (FK guard)
    db.query(models.OutfitItem).filter(models.OutfitItem.item_id == item_id).delete(synchronize_session=False)
    # delete the item — cascades remove item_states, item_stats, item_tags
    db.delete(item)
    db.commit()

    image_removed = _delete_image_file(image_path)
    return {"ok": True, "image_removed": image_removed}

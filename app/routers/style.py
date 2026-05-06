"""Style-pack export/import HTTP endpoints.

Phase 0 of the future "share your style" feature: lets a user dump
their currently-active profile's trained models into a portable
.odstyle file, and load someone else's pack into their own profile's
``models/imported/`` directory. Activation (swapping
``models/current.json`` to point at a borrowed model) is deliberately
out of scope here — the format + file IO are what need to be locked
down so packs produced today still load tomorrow.

Endpoints:
    GET  /style/export       → download the active profile's pack
    POST /style/import       → upload a pack
    GET  /style/imports      → list previously-imported packs
    DELETE /style/imports/{id} → remove an import

The actual format spec + validation lives in
``app.services.style_pack``; this router is just HTTP glue.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import branding, config, crud, models
from ..database import get_db
from ..services import style_pack


router = APIRouter(prefix="/style", tags=["style"])


@router.get("/export")
def export(db: Session = Depends(get_db)):
    """Download a `.odstyle` ZIP of the active profile's trained models.

    Always uses the live wardrobe.db to compute the n_items / n_ratings
    stats stamped into the manifest — those don't gate the import, just
    let the recipient UI show "trained on N items / M ratings."
    """
    data_dir = config.get_data_dir()
    if data_dir is None:
        raise HTTPException(status_code=400, detail="no active profile")

    profile = config.get_active_profile() or {}
    profile_name = profile.get("name") or "anon"

    n_items = (
        db.query(models.Item)
        .filter(models.Item.is_active == True)  # noqa: E712
        .count()
    )
    n_ratings = db.query(models.Rating).count()

    try:
        payload = style_pack.export_pack(
            data_dir,
            profile_name,
            n_items=n_items,
            n_ratings=n_ratings,
        )
    except style_pack.StylePackError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = style_pack.suggested_filename(profile_name)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_(file: UploadFile = File(...)):
    """Accept a uploaded `.odstyle` file, validate, and unpack into the
    active profile's `models/imported/` dir. Returns a JSON record
    describing the import."""
    data_dir = config.get_data_dir()
    if data_dir is None:
        raise HTTPException(status_code=400, detail="no active profile")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="empty file")
    # Soft size cap: a fully populated pack is well under 5 MB.
    if len(contents) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"pack is suspiciously large ({len(contents)} bytes)",
        )

    try:
        result = style_pack.import_pack(contents, data_dir)
    except style_pack.StylePackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/imports")
def list_imports():
    """List previously-imported style packs in the active profile."""
    data_dir = config.get_data_dir()
    if data_dir is None:
        return {"imports": []}
    return {"imports": style_pack.list_imported(data_dir)}


@router.delete("/imports/{import_id}")
def delete_import(import_id: str):
    """Remove a previously-imported style pack folder."""
    data_dir = config.get_data_dir()
    if data_dir is None:
        raise HTTPException(status_code=400, detail="no active profile")
    if not style_pack.remove_imported(data_dir, import_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "import_id": import_id}


@router.get("/format")
def format_info():
    """Tells the client what pack format this build understands. Useful
    for the UI to show "supports format vN" without parsing branding.py."""
    return {
        "format": branding.STYLE_PACK_FORMAT,
        "format_version": branding.STYLE_PACK_FORMAT_VERSION,
        "extension": branding.STYLE_PACK_EXTENSION,
    }

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, crud, database, models


router = APIRouter(prefix="/profiles", tags=["profiles"])


class ProfileOut(BaseModel):
    name: str
    data_dir: str


class ProfilesList(BaseModel):
    profiles: List[ProfileOut]
    active: Optional[str] = None
    profiles_root: str


class ProfileAdd(BaseModel):
    name: str


class ProfileSwitch(BaseModel):
    name: str


def _bind_and_init():
    """After switching/adding profile, rebind engine + run migrations + ensure default user."""
    database.rebind_engine_to_current_config()
    models.Base.metadata.create_all(bind=database.engine)
    db = database.SessionLocal()
    try:
        database.run_all_migrations(db)
        crud.get_or_create_default_user(db)
    finally:
        db.close()


@router.get("", response_model=ProfilesList)
def list_all():
    profiles = config.list_profiles()
    active = config.get_active_profile()
    return ProfilesList(
        profiles=[ProfileOut(**p) for p in profiles],
        active=active["name"] if active else None,
        profiles_root=str(config.PROFILES_ROOT),
    )


@router.post("")
def add(payload: ProfileAdd):
    """Create a new profile under <project>/profiles/<name>/."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "Name cannot contain path separators or start with a dot")
    if not config.add_profile(name):
        raise HTTPException(400, f"Profile name already exists: {name}")
    _bind_and_init()
    return {"ok": True, "name": name, "data_dir": str(config.PROFILES_ROOT / name)}


@router.put("/active")
def switch(payload: ProfileSwitch):
    if not config.set_active_profile(payload.name):
        raise HTTPException(404, f"Profile not found: {payload.name}")
    _bind_and_init()
    return {"ok": True, "active": payload.name}


@router.delete("/{name}")
def remove(name: str):
    if not config.remove_profile(name):
        raise HTTPException(404, f"Profile not found: {name}")
    if config.get_active_profile():
        _bind_and_init()
    return {"ok": True}

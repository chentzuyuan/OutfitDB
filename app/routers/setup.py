from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, database, models


router = APIRouter(prefix="/setup", tags=["setup"])


class SetupRequest(BaseModel):
    name: str = "Me"


class SetupStatus(BaseModel):
    setup_complete: bool
    active: str | None
    profiles_root: str
    profile_count: int


@router.get("/status", response_model=SetupStatus)
def status():
    active = config.get_active_profile()
    profiles = config.list_profiles()
    return SetupStatus(
        setup_complete=config.is_setup_complete(),
        active=active["name"] if active else None,
        profiles_root=str(config.PROFILES_ROOT),
        profile_count=len(profiles),
    )


def _activate_profile(name: str) -> None:
    """Switch the active profile + rebuild the SQLAlchemy engine + run
    schema migrations, so subsequent requests hit the right wardrobe.db."""
    config.set_active_profile(name)
    database.rebind_engine_to_current_config()
    models.Base.metadata.create_all(bind=database.engine)
    db = database.SessionLocal()
    try:
        database.run_all_migrations(db)
        from .. import crud
        crud.get_or_create_default_user(db)
    finally:
        db.close()


@router.post("")
def do_setup(req: SetupRequest):
    name = (req.name or "Me").strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if name == "Tester":
        raise HTTPException(400, "Tester is the bundled sample profile — please pick another name")
    if not config.add_profile(name):
        raise HTTPException(400, f"Profile name already exists: {name}")
    _activate_profile(name)
    return {
        "ok": True,
        "name": name,
        "data_dir": str(config.PROFILES_ROOT / name),
        "message": "Profile created — let's go",
    }


@router.post("/use_sample")
def use_sample_profile():
    """Skip the welcome flow and start the app on the bundled Tester
    sample wardrobe. The user can still add their own profile any time
    via the top-right profile switcher."""
    profiles = config.list_profiles()
    if not any(p["name"] == "Tester" for p in profiles):
        raise HTTPException(400, "Tester sample wardrobe not available on this install")
    _activate_profile("Tester")
    return {"ok": True, "name": "Tester", "message": "Using sample wardrobe"}

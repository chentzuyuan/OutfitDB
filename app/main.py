import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from . import crud, config
from .database import Base, engine, SessionLocal, run_all_migrations
from .routers import items, outfits, ratings, recommendations, contexts, train, setup, settings as settings_router, stats as stats_router, profiles as profiles_router, training as training_router, training_v2 as training_v2_router
from .version import APP_VERSION, get_version_status


# When packaged via PyInstaller, app/main.py lives inside a frozen archive
# and Path(__file__).resolve().parent points into the archive (no static
# files there). PyInstaller exposes the unpacked-resources dir at
# sys._MEIPASS — the .spec file copies app/static/ and app/templates/
# into that path, so we resolve BASE_DIR there in frozen mode.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys._MEIPASS) / "app"
else:
    BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
UPLOADS_DIR = STATIC_DIR / "uploads"
# In frozen mode the bundle is read-only, so don't try to mkdir into it.
# The legacy uploads dir is a Phase-3 fallback only used when get_data_dir()
# returns None (pre-setup); production writes go to data_dir/items/images/.
if not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "css").mkdir(parents=True, exist_ok=True)
    (STATIC_DIR / "js").mkdir(parents=True, exist_ok=True)




@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrate legacy ~/.closetmind/config.json → profiles/ folder layout (idempotent;
    # the legacy ~/.closetmind dir is also auto-mirrored into ~/.outfitdb when
    # app.config is imported, so users upgrading from <0.2.0 keep their data).
    config.migrate_legacy_config()
    # First-run convenience: when the .app or the Render container ship a
    # bundled Tester profile, copy it into PROFILES_ROOT so the app is
    # immediately playable. On Render this also force-activates Tester
    # (seed_default_profile_if_empty consults OUTFITDB_RENDER_MODE)
    # so visitors land directly on the demo wardrobe without going
    # through /setup.
    seeded = config.seed_default_profile_if_empty()
    if seeded:
        print(f"[lifespan] seeded default profile: {seeded}")
    # In Render mode the engine was created at import time pointing at
    # the legacy fallback (because seed hadn't run yet); rebind now that
    # PROFILES_ROOT/Tester/wardrobe.db exists.
    if config.RENDER_MODE:
        from .database import rebind_engine_to_current_config
        rebind_engine_to_current_config()
    if config.is_setup_complete():
        # Re-import the (possibly rebound) engine so create_all targets
        # the current per-profile DB rather than the import-time one.
        from . import database as _database
        _database.Base.metadata.create_all(bind=_database.engine)
        db = _database.SessionLocal()
        try:
            run_all_migrations(db)
            crud.get_or_create_default_user(db)
        finally:
            db.close()
    yield


app = FastAPI(title="OutfitDB", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


app.include_router(setup.router)
app.include_router(profiles_router.router)
app.include_router(settings_router.router)
app.include_router(stats_router.router)
app.include_router(items.router)
app.include_router(contexts.router)
app.include_router(outfits.router)
app.include_router(ratings.router)
app.include_router(recommendations.router)
app.include_router(train.router)
app.include_router(training_router.router)
app.include_router(training_v2_router.router)


@app.middleware("http")
async def setup_gate(request: Request, call_next):
    """If user hasn't completed setup → redirect to /setup (except for static / setup itself / healthz / version)."""
    path = request.url.path
    # /version is whitelisted because the footer (rendered on every page,
    # including /setup) calls it on load to surface the auto-update banner.
    # If we redirected /version to /setup, footer fetch returns HTML and
    # the JSON parse silently fails on every page.
    allow_prefixes = ("/static/", "/setup", "/healthz", "/version", "/images/")
    if not config.is_setup_complete() and not path.startswith(allow_prefixes):
        if path == "/setup" or path.startswith("/setup/"):
            return await call_next(request)
        if request.method == "GET":
            return RedirectResponse(url="/setup", status_code=302)
    return await call_next(request)


@app.get("/images/{filename}")
def serve_image(filename: str):
    """Serve images from data_dir/items/images/ (local-first mode)."""
    data_dir = config.get_data_dir()
    if data_dir is None:
        # legacy fallback
        legacy = STATIC_DIR / "uploads" / "1" / filename
        if legacy.exists():
            return FileResponse(legacy)
        return FileResponse(STATIC_DIR / "404.png", status_code=404)
    path = data_dir / "items" / "images" / filename
    if not path.exists():
        return FileResponse(STATIC_DIR / "404.png", status_code=404)
    return FileResponse(path)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request})


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})


@app.get("/closet", response_class=HTMLResponse)
def closet_page(request: Request):
    return templates.TemplateResponse("closet.html", {"request": request})


@app.get("/training", response_class=HTMLResponse)
def training_page(request: Request):
    return templates.TemplateResponse("training.html", {"request": request})


@app.get("/training/temperature", response_class=HTMLResponse)
def training_temp_page(request: Request):
    return templates.TemplateResponse("training_temperature.html", {"request": request})


@app.get("/training/aesthetic", response_class=HTMLResponse)
def training_aesthetic_page(request: Request):
    return templates.TemplateResponse("training_aesthetic.html", {"request": request})


@app.get("/training/occasion", response_class=HTMLResponse)
def training_occasion_page(request: Request):
    return templates.TemplateResponse("training_occasion.html", {"request": request})


@app.get("/recommend", response_class=HTMLResponse)
def recommend_page(request: Request):
    return templates.TemplateResponse("recommendation.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


@app.get("/healthz")
def healthz():
    return {"ok": True, "setup_complete": config.is_setup_complete(), "version": APP_VERSION}


@app.get("/version")
def version():
    """Returns current app version + cached remote-check result. Frontend
    calls this on page load to show 'update available' banner when a
    newer version is published. The remote check is cached server-side
    for 6 hours (see app/version.py)."""
    return get_version_status()

import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_data_dir

BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = BASE_DIR / "data"
LEGACY_DATA_DIR.mkdir(exist_ok=True)


def _resolve_db_url() -> str:
    """Priority: DATABASE_URL env > config.json data_dir > legacy fallback."""
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url.replace("postgres://", "postgresql://", 1) if env_url.startswith("postgres://") else env_url
    data_dir = get_data_dir()
    if data_dir is not None:
        return f"sqlite:///{data_dir / 'wardrobe.db'}"
    # Pre-setup fallback: keep importable so /setup page can run
    return f"sqlite:///{LEGACY_DATA_DIR / 'outfitdb.db'}"


DATABASE_URL = _resolve_db_url()

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def rebind_engine_to_current_config() -> None:
    """After /setup writes config.json, swap engine + session to point at the new DB.
    Avoids requiring a server restart on first-run setup.
    """
    global DATABASE_URL, engine, SessionLocal
    DATABASE_URL = _resolve_db_url()
    if DATABASE_URL.startswith("sqlite"):
        engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    SessionLocal.configure(bind=engine)


# ─── Schema migration helpers (idempotent ALTER TABLE) ──────────────────
ITEMS_MIGRATIONS = [
    ("sleeve", "VARCHAR(16)"),
    ("wears_per_wash", "INTEGER DEFAULT 3 NOT NULL"),
    ("composition", "JSON"),  # nullable; null = 100% of `material`
]
USERS_MIGRATIONS = [
    ("lat", "FLOAT"),
    ("lon", "FLOAT"),
    ("city", "VARCHAR(64)"),
    ("timezone", "VARCHAR(64)"),
    ("training_complete", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("temp_offset", "FLOAT DEFAULT 0.0 NOT NULL"),
    # Phase 4 — multi-stage training state
    ("v2_temp_done", "BOOLEAN DEFAULT 0 NOT NULL"),       # stage 1 finished
    ("v2_aesthetic_done", "BOOLEAN DEFAULT 0 NOT NULL"),  # stage 2 finished
    ("v2_occasion_done", "BOOLEAN DEFAULT 0 NOT NULL"),   # stage 3 finished
    ("zone_warmth_prefs", "TEXT"),                        # JSON dict — adaptive sampling state
    ("temp_unit", "VARCHAR(1) DEFAULT 'C' NOT NULL"),     # 'C' or 'F'; DB still stores Celsius
    ("has_thermal_insoles", "BOOLEAN DEFAULT 0 NOT NULL"), # personal cold-tolerance bonus
]
RATINGS_MIGRATIONS = [
    ("ideal_temp_zone", "VARCHAR(8)"),
]


def _alter_add_columns(db, table: str, plan: list) -> None:
    try:
        cols = {r[1] for r in db.execute(text(f"PRAGMA table_info({table})")).fetchall()}
    except Exception:
        return
    for col, ddl in plan:
        if col not in cols:
            try:
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            except Exception:
                pass
    db.commit()


def run_all_migrations(db) -> None:
    _alter_add_columns(db, "users", USERS_MIGRATIONS)
    _alter_add_columns(db, "items", ITEMS_MIGRATIONS)
    _alter_add_columns(db, "ratings", RATINGS_MIGRATIONS)

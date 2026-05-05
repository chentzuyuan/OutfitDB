"""Local-first config: profiles auto-discovered from <project>/profiles/.
Each subfolder of profiles/ that contains an `items/` subdir is a profile.
Active profile is remembered in ~/.closetmind/active (per-user, per-project).

This means: zip the whole wardrobe_env/ folder, send to anyone — they unzip,
run the server, and Bruce + Clark profiles auto-show. No path config needed.
"""
import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional


# closetmind/app/config.py → wardrobe_env/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Where the per-profile wardrobe.db / images / models live.
#
# Dev mode: PROJECT_ROOT/profiles/ — convenient because it lives next to
#   the source tree and shows up in `ls`.
#
# Frozen-app mode: a USER-WRITABLE directory outside the .app bundle.
#   Writing inside .app/Contents would (a) violate macOS code-sign
#   integrity, (b) be wiped on every app update, (c) be blocked by
#   Gatekeeper on some configurations. Use the OS-standard app-data
#   location instead so user data survives app updates and reinstalls.
def _default_profiles_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "ClosetMind" / "profiles"
        if sys.platform == "win32":
            base = os.getenv("APPDATA") or str(Path.home())
            return Path(base) / "ClosetMind" / "profiles"
        # Linux / other Unix — XDG Base Directory spec
        xdg = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(xdg) / "ClosetMind" / "profiles"
    return PROJECT_ROOT / "profiles"


DEFAULT_PROFILES_ROOT = _default_profiles_root()

# Override via env var (e.g., for cloud deployment / testing)
PROFILES_ROOT = Path(os.getenv("CLOSETMIND_PROFILES_ROOT") or DEFAULT_PROFILES_ROOT).resolve()

# Per-user state (which profile is active). Not part of project.
USER_STATE_DIR = Path.home() / ".closetmind"
ACTIVE_FILE = USER_STATE_DIR / "active"

# Legacy formats — migrated on first run
LEGACY_CONFIG_FILE = USER_STATE_DIR / "config.json"


def _ensure_dirs():
    PROFILES_ROOT.mkdir(parents=True, exist_ok=True)
    USER_STATE_DIR.mkdir(parents=True, exist_ok=True)


RENDER_MODE = bool(os.getenv("CLOSETMIND_RENDER_MODE"))


def _locate_tester_seed() -> Optional[Path]:
    """Find a `Tester` directory we can copy into PROFILES_ROOT. Three
    candidate locations, in priority order:
      1. PyInstaller frozen bundle (.app on macOS) — sys._MEIPASS/seed_profiles/Tester
      2. Project sibling dir — wardrobe_env/profiles/Tester (dev source tree)
      3. Project bundled seed — closetmind/seed_profiles/Tester (Render deploy)"""
    candidates: List[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "seed_profiles" / "Tester")
    candidates.append(PROJECT_ROOT / "profiles" / "Tester")
    candidates.append(PROJECT_ROOT / "closetmind" / "seed_profiles" / "Tester")
    candidates.append(Path(__file__).resolve().parent.parent / "seed_profiles" / "Tester")
    for c in candidates:
        if c.is_dir() and (c / "wardrobe.db").exists():
            return c
    return None


def seed_default_profile_if_empty() -> Optional[str]:
    """First-run seed: drop the bundled Tester profile into PROFILES_ROOT
    if it isn't already there. Always runs on launch (not gated on the
    user having zero profiles), so even after the user creates their own
    profile they keep Tester as a sample wardrobe to compare against in
    the top-right profile switcher.

    On RENDER mode the function additionally writes ACTIVE_FILE = Tester
    so visitors land directly on the demo wardrobe without going through
    the /setup welcome flow. In normal desktop mode ACTIVE_FILE is left
    untouched, so the user still sees /setup until they create their
    own profile.

    Idempotent: noop if PROFILES_ROOT/Tester already exists."""
    _ensure_dirs()
    seed_root = _locate_tester_seed()
    if seed_root is None:
        return None
    target = PROFILES_ROOT / "Tester"
    if not target.exists():
        try:
            shutil.copytree(seed_root, target)
        except Exception as exc:  # noqa: BLE001
            print(f"[config] seed Tester failed: {exc}")
            return None
    if RENDER_MODE and not ACTIVE_FILE.exists():
        ACTIVE_FILE.write_text("Tester")
    return "Tester"


def _is_profile_folder(path: Path) -> bool:
    """A profile folder must have either a wardrobe.db or canonical subfolders."""
    if not path.is_dir() or path.name.startswith("."):
        return False
    if (path / "wardrobe.db").exists():
        return True
    return (path / "items").exists()


def list_profiles() -> List[dict]:
    if not PROFILES_ROOT.exists():
        return []
    out = []
    for child in sorted(PROFILES_ROOT.iterdir()):
        if _is_profile_folder(child):
            out.append({"name": child.name, "data_dir": str(child)})
    return out


def get_active_profile() -> Optional[dict]:
    profiles = list_profiles()
    if not profiles:
        return None
    if ACTIVE_FILE.exists():
        try:
            name = ACTIVE_FILE.read_text().strip()
            for p in profiles:
                if p["name"] == name:
                    return p
        except Exception:
            pass
    # Fallback: first alphabetical
    return profiles[0]


def set_active_profile(name: str) -> bool:
    if not any(p["name"] == name for p in list_profiles()):
        return False
    _ensure_dirs()
    ACTIVE_FILE.write_text(name)
    return True


def add_profile(name: str) -> bool:
    """Create a new profile folder. Returns False if name conflicts or invalid."""
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return False
    target = PROFILES_ROOT / name
    if target.exists():
        return False
    ensure_data_dir_structure(target)
    if not ACTIVE_FILE.exists():
        set_active_profile(name)
    return True


def remove_profile(name: str) -> bool:
    target = PROFILES_ROOT / name
    if not target.exists() or not target.is_dir():
        return False
    try:
        shutil.rmtree(target)
    except Exception:
        return False
    if ACTIVE_FILE.exists() and ACTIVE_FILE.read_text().strip() == name:
        remaining = list_profiles()
        if remaining:
            set_active_profile(remaining[0]["name"])
        else:
            ACTIVE_FILE.unlink(missing_ok=True)
    return True


def get_data_dir() -> Optional[Path]:
    """Returns active profile's data folder. None if no profile or cloud mode."""
    if os.getenv("DATABASE_URL"):
        return None
    p = get_active_profile()
    if not p:
        return None
    pp = Path(p["data_dir"]).expanduser().resolve()
    return pp if pp.exists() else None


def ensure_data_dir_structure(data_dir: Path) -> None:
    data_dir = Path(data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "items" / "images").mkdir(parents=True, exist_ok=True)
    (data_dir / "models" / "archive").mkdir(parents=True, exist_ok=True)
    readme = data_dir / "README.txt"
    if not readme.exists():
        readme.write_text(
            "ClosetMind profile folder\n"
            "==========================\n\n"
            "wardrobe.db        — SQLite (items / ratings / outfits / contexts ...)\n"
            "items/images/      — clothing photos\n"
            "models/current.json — XGBoost preference model\n"
            "models/archive/    — older models, auto-archived on retrain\n",
            encoding="utf-8",
        )


def is_setup_complete() -> bool:
    """Three independent ways to count onboarding as done:

      1. DATABASE_URL set — legacy cloud-Postgres mode.
      2. RENDER_MODE set and the Tester sample is present — Render
         demo deploy: Tester is the *intended* active profile, not a
         placeholder, so don't gate the app behind /setup.
      3. The user owns at least one non-Tester profile — desktop
         install: they've gone through /setup or imported their own
         wardrobe. Tester being the only profile means the user is
         on first-launch and hasn't picked anything yet.
    """
    if os.getenv("DATABASE_URL"):
        return True
    profiles = list_profiles()
    if not profiles:
        return False
    if RENDER_MODE:
        return any(p["name"] == "Tester" for p in profiles)
    return any(p["name"] != "Tester" for p in profiles)


def reset_active() -> None:
    if ACTIVE_FILE.exists():
        ACTIVE_FILE.unlink()


def migrate_legacy_config() -> None:
    """Old format: ~/.closetmind/config.json with absolute data_dirs.
    New format: profiles/* folders + ~/.closetmind/active.
    Idempotent: safe to call repeatedly. Migrates folders into profiles/, then
    backs up the legacy config so we don't redo this.
    """
    if not LEGACY_CONFIG_FILE.exists():
        return
    try:
        cfg = json.loads(LEGACY_CONFIG_FILE.read_text())
    except Exception:
        return
    profiles = cfg.get("profiles", [])
    active = cfg.get("active")
    _ensure_dirs()
    for p in profiles:
        name = p.get("name")
        path_str = p.get("data_dir")
        if not name or not path_str:
            continue
        old = Path(path_str).expanduser().resolve()
        new = PROFILES_ROOT / name
        if old.exists() and old.is_dir() and not new.exists():
            try:
                shutil.move(str(old), str(new))
            except Exception:
                pass
    if active and (PROFILES_ROOT / active).exists():
        ACTIVE_FILE.write_text(active)
    # back up legacy config so we don't run this again
    try:
        LEGACY_CONFIG_FILE.rename(LEGACY_CONFIG_FILE.with_suffix(".json.bak"))
    except Exception:
        pass

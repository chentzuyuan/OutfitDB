"""Single source of truth for the app's brand identity.

Every brand-coupled string elsewhere in the codebase reads from this
module. Future renames are a one-file edit (plus the BRANDING.md
checklist for the few external surfaces that aren't part of the running
Python process — repo dir name on disk, GitHub repo slug, Render service
name, etc.).

Usage from Python::

    from app import branding
    print(branding.APP_NAME)              # 'OutfitDB'
    print(branding.user_state_dir())      # PosixPath('/Users/.../.outfitdb')

Usage from Jinja templates::

    <title>{{ brand.app_name }} · Settings</title>

Usage from JS (every page loads this via base.html)::

    window.OD_BRAND.appName       // 'OutfitDB'
    window.OD_BRAND.lsPrefix      // 'od_'
    window.OD_BRAND.eventPrefix   // 'od-'

Adding a new brand-coupled string anywhere in the codebase: extend this
module first, never hardcode the literal.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import List


# ─── Names ──────────────────────────────────────────────────────────────
# Display name (sentence case, what users see).
APP_NAME: str = "OutfitDB"
# Lowercase identifier — folder names, env vars, URL slugs.
APP_NAME_LOWER: str = "outfitdb"
# Truncated form for tight UI (PWA short_name etc.). Kept identical to
# APP_NAME today; split out so a longer brand can shorten in the future.
APP_NAME_SHORT: str = APP_NAME
# Marketing one-liner (used in PWA manifest description + repo About).
APP_DESCRIPTION_ZH: str = "本地優先的穿搭排程系統"
APP_DESCRIPTION_EN: str = "Local-first outfit recommendation app"


# ─── On-disk identifiers ────────────────────────────────────────────────
# Per-user state dir under $HOME (active-profile pointer + legacy
# config). Pre-0.2.0 was ".closetmind"; LEGACY_USER_STATE_DIRNAMES below
# captures that for migration.
USER_STATE_DIRNAME: str = f".{APP_NAME_LOWER}"
# Frozen-app data dir basename, used inside platform-specific roots:
#   macOS:   ~/Library/Application Support/<APP_DATA_DIRNAME>/profiles
#   Windows: %APPDATA%\<APP_DATA_DIRNAME>\profiles
#   Linux:   $XDG_DATA_HOME/<APP_DATA_DIRNAME>/profiles
APP_DATA_DIRNAME: str = APP_NAME
# What the /setup page suggests as a default user-data dir.
DEFAULT_DATA_DIR_NAME: str = f"My{APP_NAME}"
# Per-profile SQLite filename (kept platform-neutral; the brand isn't in
# the filename so renames don't migrate every wardrobe.db on disk).
WARDROBE_DB_FILENAME: str = "wardrobe.db"
# Pre-setup fallback DB filename used when no profile is active yet.
LEGACY_FALLBACK_DB_FILENAME: str = f"{APP_NAME_LOWER}.db"


# ─── Bundle / OS-level identifiers ──────────────────────────────────────
APP_BUNDLE_ID: str = f"com.{APP_NAME_LOWER}.app"
# Reverse-DNS prefix used for any platform-specific identifier we emit
# (URL schemes, future deep-link support, etc.).
APP_REVERSE_DOMAIN: str = f"com.{APP_NAME_LOWER}"


# ─── JS / browser-side identifiers ──────────────────────────────────────
# JS global namespace (window.<JS_NAMESPACE>).
JS_NAMESPACE: str = "od"
# localStorage key prefix. Every persistent client-side key starts with
# this — od_temp_unit, od_lang, od_skipped_steps, etc. Future brand
# changes update this; the i18n + temp_unit modules read it from
# window.OD_BRAND so nothing else needs to change.
LS_PREFIX: str = "od_"
# Custom-event prefix for cross-script signalling (od-temp-unit-change).
EVENT_PREFIX: str = "od-"


# ─── Distribution / public surface ──────────────────────────────────────
GITHUB_OWNER: str = "chentzuyuan"
GITHUB_REPO: str = APP_NAME                              # chentzuyuan/OutfitDB
RELEASES_REPO: str = f"buttegg/{APP_NAME_LOWER}-releases"
HOMEPAGE_URL: str = f"https://{APP_NAME_LOWER}.onrender.com/"
DEFAULT_UPDATE_FEED: str = (
    f"https://raw.githubusercontent.com/{RELEASES_REPO}/main/latest.json"
)


# ─── Style-pack format ──────────────────────────────────────────────────
# File extension for shareable trained-model bundles (Phase B).
STYLE_PACK_EXTENSION: str = ".odstyle"
# MIME-ish identifier written into the manifest so the importer can
# refuse files from incompatible apps that happen to share the .zip
# wrapper. Updated whenever the format itself changes shape.
STYLE_PACK_FORMAT: str = "outfitdb-style-pack"
STYLE_PACK_FORMAT_VERSION: int = 1


# ─── Legacy back-compat (used during migration / fallback lookups) ──────
# Past brand names. New entries go to the FRONT of the list — the order
# is "most recent legacy first" so cross-rename migration probes work
# even after a future rename.
LEGACY_NAMES: List[str] = ["ClosetMind"]
LEGACY_USER_STATE_DIRNAMES: List[str] = [".closetmind"]
LEGACY_APP_DATA_DIRNAMES: List[str] = ["ClosetMind"]
LEGACY_LS_PREFIXES: List[str] = ["cm_"]
LEGACY_EVENT_PREFIXES: List[str] = ["cm-"]
LEGACY_BUNDLE_IDS: List[str] = ["com.closetmind.app"]
# Render / hosting env-var prefixes from previous brands. Kept honoured
# in app/config.py + app/version.py so old deploys keep working through
# the rename.
LEGACY_ENV_PREFIXES: List[str] = ["CLOSETMIND_"]


# ─── Helpers ────────────────────────────────────────────────────────────
def env_var(suffix: str) -> str:
    """Return the canonical env-var name for a given setting key.

    Example::

        env_var("PROFILES_ROOT")  # 'OUTFITDB_PROFILES_ROOT'
    """
    return f"{APP_NAME_LOWER.upper()}_{suffix}"


def legacy_env_vars(suffix: str) -> List[str]:
    """All historical env-var names for the given setting key, ordered
    most-recent-first. config.py reads the canonical name first and
    falls back through this list.

    Example::

        legacy_env_vars("PROFILES_ROOT")  # ['CLOSETMIND_PROFILES_ROOT']
    """
    return [f"{p}{suffix}" for p in LEGACY_ENV_PREFIXES]


def resolve_env(suffix: str) -> str | None:
    """Read the first non-empty env-var across canonical + legacy names.

    Example::

        resolve_env("UPDATE_FEED")
        # tries OUTFITDB_UPDATE_FEED, then CLOSETMIND_UPDATE_FEED
    """
    for name in [env_var(suffix), *legacy_env_vars(suffix)]:
        v = os.environ.get(name)
        if v:
            return v
    return None


def user_state_dir() -> Path:
    """~/<USER_STATE_DIRNAME> — per-user pointer file location."""
    return Path.home() / USER_STATE_DIRNAME


def legacy_user_state_dirs() -> List[Path]:
    return [Path.home() / d for d in LEGACY_USER_STATE_DIRNAMES]


def jinja_globals() -> dict:
    """Subset of brand identifiers exposed to Jinja templates as
    ``brand.*``. Keep this minimal — anything broader should be wired
    up explicitly per template."""
    return {
        "app_name": APP_NAME,
        "app_name_lower": APP_NAME_LOWER,
        "app_name_short": APP_NAME_SHORT,
        "description_zh": APP_DESCRIPTION_ZH,
        "description_en": APP_DESCRIPTION_EN,
        "homepage_url": HOMEPAGE_URL,
        "github_repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
    }


def js_globals() -> dict:
    """JSON-serialisable bundle injected as ``window.OD_BRAND`` from
    base.html. Whatever JS reads at runtime to find the brand prefix /
    event names / app name lives here."""
    return {
        "appName": APP_NAME,
        "appNameLower": APP_NAME_LOWER,
        "appNameShort": APP_NAME_SHORT,
        "namespace": JS_NAMESPACE,
        "lsPrefix": LS_PREFIX,
        "eventPrefix": EVENT_PREFIX,
        "legacyLsPrefixes": LEGACY_LS_PREFIXES,
        "legacyEventPrefixes": LEGACY_EVENT_PREFIXES,
        "stylePackExtension": STYLE_PACK_EXTENSION,
    }

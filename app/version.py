"""App version + auto-update check.

Single source of truth for the application version, plus a cached remote
check against a publicly-readable JSON manifest. The manifest is
deliberately decoupled from the source repo (which can stay private)
so we can ship versions without exposing code.

Brand-coupled identifiers (User-Agent header, default update feed URL)
all read from app/branding.py — renaming the app is a one-file edit.

Manifest format (latest.json on the public URL):
    {
        "version": "0.3.0",
        "url": "https://example.com/MyApp-0.3.0.dmg",      # legacy single
        "update_url": "https://.../OutfitDB-0.3.0-update.zip",  # incremental
        "full_url":   "https://.../OutfitDB-0.3.0-full.dmg",    # full installer
        "notes": "Adds X / fixes Y"
    }

`update_url` and `full_url` are optional — if either is missing we fall
back to the legacy `url`. The UI surfaces both when present so the user
can pick a smaller incremental download or a fresh full installer.

The `/version` endpoint returns:
    {
        "current": "0.3.0",
        "latest": "0.4.0" | null,
        "update_available": true | false,
        "url": "...",         # always populated if any of the three is set
        "update_url": "..." | null,
        "full_url":   "..." | null,
        "notes": "..."
    }

`null` for `latest` means the check failed (offline, server down, etc.)
— the UI just shows the current version with no banner in that case.
"""
from __future__ import annotations
import time
import threading
from typing import Optional
import urllib.request
import urllib.error
import json

from . import branding


APP_VERSION = "0.3.1"

# Public URL serving the latest-version manifest. We use a GitHub raw URL
# pointing at a SEPARATE public releases repo so the source repo stays
# private. Owner is responsible for maintaining this manifest.
#
# Override via env var (canonical OUTFITDB_UPDATE_FEED, or any legacy
# brand-prefixed alias such as CLOSETMIND_UPDATE_FEED). branding.resolve_env
# walks the canonical + legacy names so old custom-feed setups don't
# silently break after a rename.
UPDATE_FEED_URL = (
    branding.resolve_env("UPDATE_FEED") or branding.DEFAULT_UPDATE_FEED
)

# Cache the remote check for 6 hours so we don't pound the GitHub raw
# CDN on every page load. The cache lives in process memory only — fine
# because the app is restarted on every desktop launch anyway.
_CHECK_TTL_SECONDS = 6 * 3600
_cache_lock = threading.Lock()
_cache: dict = {"checked_at": 0.0, "result": None}


def _parse_version(v: str) -> tuple:
    """Lenient SemVer parse: '0.1.0' → (0, 1, 0). Strings that don't parse
    cleanly compare as lower than any parsable version."""
    try:
        parts = [int(p) for p in str(v).strip().lstrip("v").split(".")]
        return tuple(parts + [0] * (3 - len(parts)))[:3]
    except Exception:
        return (-1, -1, -1)


def _is_newer(remote: Optional[str], local: str) -> bool:
    if not remote:
        return False
    return _parse_version(remote) > _parse_version(local)


def _fetch_remote() -> Optional[dict]:
    """One-shot fetch of the manifest. Returns parsed dict or None on any
    failure (network, JSON parse, HTTP error). Times out fast so a slow
    GitHub raw doesn't block app startup."""
    try:
        req = urllib.request.Request(
            UPDATE_FEED_URL,
            headers={"User-Agent": f"{branding.APP_NAME}/{APP_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
        return json.loads(data)
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None


def is_web_demo() -> bool:
    """True when the app is running as the public web demo (Render etc.).
    The desktop app — packaged with PyInstaller — sets sys.frozen, so the
    inverse check is a reliable web detector. Operators can also force
    demo mode via the OUTFITDB_WEB_DEMO env var (e.g. for staging hosts).
    """
    import os, sys
    if os.environ.get("OUTFITDB_WEB_DEMO", "").strip() in ("1", "true", "yes"):
        return True
    # PyInstaller-bundled apps set sys.frozen — anything else (including
    # uvicorn on Render) counts as web.
    return not getattr(sys, "frozen", False)


# Backwards-compat private alias — used inside this module.
_is_web_demo = is_web_demo


def get_version_status() -> dict:
    """Return the current version + cached remote-check result.

    Result shape:
        {"current": "0.1.0",
         "latest": "0.2.0" | None,
         "update_available": bool,
         "is_web_demo": bool,
         "url": str | None,         # legacy / fallback installer URL
         "update_url": str | None,  # smaller, incremental update package
         "full_url":   str | None,  # complete installer
         "notes": str | None}

    The remote check is cached for _CHECK_TTL_SECONDS, so calling this
    on every page load is cheap.
    """
    now = time.time()
    web_demo = _is_web_demo()
    with _cache_lock:
        if _cache["result"] is not None and (now - _cache["checked_at"]) < _CHECK_TTL_SECONDS:
            cached = dict(_cache["result"])  # defensive copy
            cached["is_web_demo"] = web_demo  # may have toggled at runtime
            return cached

    remote = _fetch_remote()
    if remote is None:
        result = {
            "current": APP_VERSION,
            "latest": None,
            "update_available": False,
            "is_web_demo": web_demo,
            "url": None,
            "update_url": None,
            "full_url": None,
            "notes": None,
        }
    else:
        latest = remote.get("version")
        url        = remote.get("url")
        update_url = remote.get("update_url") or url
        full_url   = remote.get("full_url")   or url
        result = {
            "current": APP_VERSION,
            "latest": latest,
            "update_available": _is_newer(latest, APP_VERSION),
            "is_web_demo": web_demo,
            "url": url,
            "update_url": update_url,
            "full_url": full_url,
            "notes": remote.get("notes"),
        }

    with _cache_lock:
        _cache["checked_at"] = now
        _cache["result"] = result
    return dict(result)

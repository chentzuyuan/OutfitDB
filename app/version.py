"""ClosetMind version + auto-update check.

Single source of truth for the application version, plus a cached remote
check against a publicly-readable JSON manifest. The manifest is
deliberately decoupled from the source repo (which can stay private)
so we can ship versions without exposing code.

Manifest format (latest.json on the public URL):
    {
        "version": "0.2.0",
        "url": "https://example.com/closetmind-0.2.0.dmg",
        "notes": "Adds X / fixes Y"
    }

The `/version` endpoint returns:
    {
        "current": "0.1.0",
        "latest": "0.2.0" | null,
        "update_available": true | false,
        "url": "...",
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


APP_VERSION = "0.1.3"

# Public URL serving the latest-version manifest. We use a GitHub raw URL
# pointing at a SEPARATE public releases repo so the source repo stays
# private. Owner is responsible for maintaining this manifest.
#
# Override with the CLOSETMIND_UPDATE_FEED env var if you want to point
# at a different feed (useful for testing or alternative distribution).
import os
UPDATE_FEED_URL = os.environ.get(
    "CLOSETMIND_UPDATE_FEED",
    "https://raw.githubusercontent.com/buttegg/closetmind-releases/main/latest.json",
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
            headers={"User-Agent": f"ClosetMind/{APP_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
        return json.loads(data)
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None


def get_version_status() -> dict:
    """Return the current version + cached remote-check result.

    Result shape:
        {"current": "0.1.0",
         "latest": "0.2.0" | None,
         "update_available": bool,
         "url": str | None,
         "notes": str | None}

    The remote check is cached for _CHECK_TTL_SECONDS, so calling this
    on every page load is cheap.
    """
    now = time.time()
    with _cache_lock:
        if _cache["result"] is not None and (now - _cache["checked_at"]) < _CHECK_TTL_SECONDS:
            return dict(_cache["result"])  # defensive copy

    remote = _fetch_remote()
    if remote is None:
        result = {
            "current": APP_VERSION,
            "latest": None,
            "update_available": False,
            "url": None,
            "notes": None,
        }
    else:
        latest = remote.get("version")
        result = {
            "current": APP_VERSION,
            "latest": latest,
            "update_available": _is_newer(latest, APP_VERSION),
            "url": remote.get("url"),
            "notes": remote.get("notes"),
        }

    with _cache_lock:
        _cache["checked_at"] = now
        _cache["result"] = result
    return dict(result)

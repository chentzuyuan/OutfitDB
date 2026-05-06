"""Cross-platform style-pack export/import.

A *style pack* is a portable bundle of one user's trained outfit
preference models so they can share their "taste" with another user.
Phase 0 (this file) is the foundation for the future style-sharing
feature: format spec + read/write + import-side validation. The UI for
*activating* a borrowed style (replace your own / blend / compare) is
out of scope here.

File format
-----------
A `.odstyle` file is a ZIP archive — works identically on macOS, Linux,
and Windows because ZIP is platform-neutral and Python's stdlib
`zipfile` produces byte-identical output across OSes. The archive
contains:

    manifest.json         — human-readable metadata (see below)
    models/aesthetic.json — XGBoost dump (stage 2, the "look" model)
    models/temperature.json — optional, stage 1 (warmth tolerance)
    models/occasion.json    — optional, stage 3 (event fit)

Manifest schema (v1)::

    {
      "format": "outfitdb-style-pack",   # branding.STYLE_PACK_FORMAT
      "format_version": 1,                # branding.STYLE_PACK_FORMAT_VERSION
      "feature_version": 1,               # feature_engineering.FEATURE_VERSION
                                          #   imports refuse non-matching values
      "exported_by": {
          "app": "OutfitDB",              # branding.APP_NAME at export time
          "app_version": "0.3.0",         # version.APP_VERSION at export time
          "profile": "Bruce",             # source profile name (display only)
      },
      "exported_at": "2026-05-06T05:30:00Z",
      "axes": ["aesthetic", "temperature", "occasion"],  # which models present
      "model_files": {
          "aesthetic":   "models/aesthetic.json",
          "temperature": "models/temperature.json",
          "occasion":    "models/occasion.json",
      },
      "stats": {
          "n_items": 54,                  # nice-to-have for the recipient UI
          "n_ratings": 753,
      }
    }

Importer rules
--------------
- ``format`` mismatch → reject (probably a different app)
- ``format_version`` higher than the importer knows → reject
- ``feature_version`` mismatch → reject (model produces garbage on
  incompatible feature layout)
- Source profile name → sanitised before being used as a folder name
  (no path traversal, no slashes)

Imports are stored under
``data_dir/models/imported/{author_safe}-{export_date}/`` so the user
can have several borrowed styles installed in parallel without
clobbering. Activation (swapping `models/current.json` to point at an
imported one) is a separate operation, not handled here.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from .. import branding
from ..version import APP_VERSION
from .feature_engineering import FEATURE_VERSION


# Filenames inside the pack — fixed by the spec, not user-tunable.
PACK_MANIFEST = "manifest.json"
PACK_MODEL_DIR = "models"
PACK_FILES_BY_AXIS = {
    "aesthetic": "models/aesthetic.json",
    "temperature": "models/temperature.json",
    "occasion": "models/occasion.json",
}

# Source-side filenames (what each axis is called inside data_dir/models/).
SOURCE_FILES_BY_AXIS = {
    "aesthetic": "current.json",
    "temperature": "stage1_temp.json",
    "occasion": "stage3_occasion.json",
}


# ─── Errors ─────────────────────────────────────────────────────────────
class StylePackError(Exception):
    """Raised when a pack can't be produced or read."""


# ─── Helpers ────────────────────────────────────────────────────────────
def _safe_name(s: str, fallback: str = "anon") -> str:
    """Reduce a free-form string to a filesystem-safe folder name.
    Strips path separators, control chars, and most punctuation."""
    s = (s or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return cleaned[:64] if cleaned else fallback


def _now_iso_z() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_compact() -> str:
    return _dt.date.today().strftime("%Y%m%d")


# ─── Export ─────────────────────────────────────────────────────────────
def export_pack(
    data_dir: Path,
    profile_name: str,
    *,
    n_items: Optional[int] = None,
    n_ratings: Optional[int] = None,
) -> bytes:
    """Bundle every available trained model from data_dir/models/ into a
    .odstyle ZIP and return its bytes. Raises StylePackError if no
    models exist (nothing useful to share).

    The two `n_*` stats are display-only; the importer never gates on
    them. Pass them in if you have them so the recipient UI can show
    "trained on 54 items / 753 ratings" — otherwise they're omitted.
    """
    data_dir = Path(data_dir)
    models_dir = data_dir / "models"
    if not models_dir.is_dir():
        raise StylePackError(f"no models directory at {models_dir}")

    # Walk the axis registry; only include models that actually exist
    # on disk. Missing models aren't an error — the user might only
    # have trained the aesthetic stage so far.
    axes_present = []
    file_payloads: dict[str, bytes] = {}
    for axis, filename in SOURCE_FILES_BY_AXIS.items():
        src = models_dir / filename
        if src.exists():
            axes_present.append(axis)
            file_payloads[PACK_FILES_BY_AXIS[axis]] = src.read_bytes()

    if not axes_present:
        raise StylePackError(
            "no trained models to export — finish at least one training "
            "axis first"
        )

    manifest = {
        "format": branding.STYLE_PACK_FORMAT,
        "format_version": branding.STYLE_PACK_FORMAT_VERSION,
        "feature_version": FEATURE_VERSION,
        "exported_by": {
            "app": branding.APP_NAME,
            "app_version": APP_VERSION,
            "profile": profile_name,
        },
        "exported_at": _now_iso_z(),
        "axes": axes_present,
        "model_files": {a: PACK_FILES_BY_AXIS[a] for a in axes_present},
        "stats": {
            k: v for k, v in (("n_items", n_items), ("n_ratings", n_ratings))
            if v is not None
        },
    }

    buf = io.BytesIO()
    # ZIP_DEFLATED keeps the archive small without requiring extra
    # libraries — XGBoost JSON dumps are mostly numeric ASCII so they
    # compress well (~6×).
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            PACK_MANIFEST,
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )
        for arcname, data in file_payloads.items():
            zf.writestr(arcname, data)
    return buf.getvalue()


def suggested_filename(profile_name: str, version: str | None = None) -> str:
    """Default download filename — ``OutfitDB-Bruce-style-20260506.odstyle``.

    Always includes the brand name so a user receiving multiple files
    can tell at a glance which app produced them. The `version` slot
    is for callers that want to embed the source app version (only
    used by the export endpoint when explicitly asked)."""
    base = f"{branding.APP_NAME}-{_safe_name(profile_name)}-style-{_today_compact()}"
    if version:
        base += f"-v{version}"
    return base + branding.STYLE_PACK_EXTENSION


# ─── Import ─────────────────────────────────────────────────────────────
def parse_manifest(zf: zipfile.ZipFile) -> dict:
    """Read manifest.json out of an open zipfile and parse it. Raises
    StylePackError on missing / malformed manifests."""
    try:
        with zf.open(PACK_MANIFEST) as fp:
            data = json.loads(fp.read().decode("utf-8"))
    except KeyError:
        raise StylePackError("not a style pack — no manifest.json")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise StylePackError(f"manifest is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise StylePackError("manifest is not a JSON object")
    return data


def validate_manifest(manifest: dict) -> None:
    """Refuse packs that are unsafe to load. Caller is expected to have
    already parsed the manifest (parse_manifest)."""
    fmt = manifest.get("format")
    if fmt != branding.STYLE_PACK_FORMAT:
        raise StylePackError(
            f"wrong format identifier ({fmt!r}) — this isn't an "
            f"{branding.APP_NAME} style pack"
        )

    fmt_ver = manifest.get("format_version")
    if not isinstance(fmt_ver, int) or fmt_ver > branding.STYLE_PACK_FORMAT_VERSION:
        raise StylePackError(
            f"unsupported pack format_version={fmt_ver}; this build "
            f"understands up to {branding.STYLE_PACK_FORMAT_VERSION}. "
            f"Update the app and try again."
        )

    feat_ver = manifest.get("feature_version")
    if feat_ver != FEATURE_VERSION:
        raise StylePackError(
            f"feature pipeline mismatch: pack was trained against "
            f"feature_version={feat_ver}, this build uses {FEATURE_VERSION}. "
            f"Inference would silently produce wrong scores; refusing to load."
        )

    axes = manifest.get("axes") or []
    if not isinstance(axes, list) or not axes:
        raise StylePackError("pack manifest declares no axes")

    files = manifest.get("model_files") or {}
    for axis in axes:
        path = files.get(axis)
        if not path or not isinstance(path, str):
            raise StylePackError(
                f"manifest is missing model_files entry for axis {axis!r}"
            )
        if path.startswith("/") or ".." in path or "\\" in path:
            raise StylePackError(
                f"manifest model_files path {path!r} is unsafe"
            )


def import_pack(zip_bytes: bytes, data_dir: Path) -> dict:
    """Validate and unpack a .odstyle into
    ``data_dir/models/imported/{author_safe}-{export_date}/``.

    Returns a small dict describing what was imported, e.g.::

        {
            "import_id": "Bruce-20260506",
            "path": ".../models/imported/Bruce-20260506",
            "axes": ["aesthetic", "temperature"],
            "manifest": {... full manifest ...},
        }

    Caller is responsible for any "activate this style" step.
    """
    if not zip_bytes:
        raise StylePackError("empty pack")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
    except zipfile.BadZipFile as e:
        raise StylePackError(f"file is not a valid zip archive: {e}") from e

    with zf:
        manifest = parse_manifest(zf)
        validate_manifest(manifest)

        # Build a unique-ish folder name from author + export date.
        author = (
            (manifest.get("exported_by") or {}).get("profile")
            or (manifest.get("exported_by") or {}).get("app")
            or "anon"
        )
        exported_at = manifest.get("exported_at", "")
        date_part = exported_at[:10].replace("-", "") or _today_compact()
        import_id = f"{_safe_name(author)}-{date_part}"
        target_dir = Path(data_dir) / "models" / "imported" / import_id
        target_dir.mkdir(parents=True, exist_ok=True)

        # Materialise each declared model file into the target dir.
        files = manifest["model_files"]
        for axis in manifest["axes"]:
            arcname = files[axis]
            try:
                with zf.open(arcname) as src:
                    data = src.read()
            except KeyError:
                raise StylePackError(
                    f"manifest claims axis {axis!r} but {arcname!r} is "
                    f"missing from the archive"
                )
            out = target_dir / Path(arcname).name
            out.write_bytes(data)

        # Drop the manifest in too — handy for debugging + the import
        # listing UI doesn't need to re-parse the zip every time.
        (target_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return {
        "import_id": import_id,
        "path": str(target_dir),
        "axes": list(manifest["axes"]),
        "manifest": manifest,
    }


def list_imported(data_dir: Path) -> list[dict]:
    """List previously-imported style packs in
    ``data_dir/models/imported/`` so the settings UI can render them.
    Each entry: {import_id, axes, exported_by, exported_at, path}."""
    root = Path(data_dir) / "models" / "imported"
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "import_id": child.name,
            "axes": manifest.get("axes", []),
            "exported_by": manifest.get("exported_by", {}),
            "exported_at": manifest.get("exported_at"),
            "stats": manifest.get("stats", {}),
            "path": str(child),
        })
    return out


def remove_imported(data_dir: Path, import_id: str) -> bool:
    """Delete an imported style pack folder. Returns True if anything
    was deleted, False if no such import. Refuses to touch paths
    outside of data_dir/models/imported/ — defends against import_id
    smuggling a path traversal."""
    safe = _safe_name(import_id)
    if not safe or safe != import_id:
        return False
    target = Path(data_dir) / "models" / "imported" / safe
    if not target.is_dir():
        return False
    import shutil
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()

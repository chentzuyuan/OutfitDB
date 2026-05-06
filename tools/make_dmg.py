"""Wrap the built .app in a macOS .dmg installer.

The brand name comes from app/branding.py — renaming the brand updates
the volume name + DMG filename automatically on the next build.

Usage (run AFTER tools/build_app.py has produced dist/<APP_NAME>.app):
    .venv/bin/python -m tools.make_dmg

Output:
    dist/<APP_NAME>-{version}.dmg

Why DMG over a plain .zip:
  - macOS users recognize the "drag-to-Applications" pattern instantly.
    Double-click the DMG → window shows the .app next to an Applications
    folder shortcut → drag the .app onto the shortcut → installed.
  - The installed .app shows up in Launchpad / Spotlight / Finder
    Applications immediately, no manual file management needed.
  - DMG itself is read-only and self-contained; user can delete it
    after install with no consequence.

Tooling: pure stdlib subprocess + macOS-built-in `hdiutil`. No homebrew
or npm dependencies required, so the build works on any developer's
machine without setup.

This script ONLY runs on macOS — the DMG format is Apple-specific.
Windows/Linux distribution paths are different (zip / installer / etc.)
and live in their own scripts when needed.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from app import branding  # noqa: E402

DIST = PROJECT_ROOT / "dist"
APP = DIST / f"{branding.APP_NAME}.app"
STAGING = DIST / "_dmg_staging"


def _read_version() -> str:
    """Pull APP_VERSION out of app/version.py without importing it
    (which would pull in the whole FastAPI stack)."""
    text = (PROJECT_ROOT / "app" / "version.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("APP_VERSION"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def main() -> None:
    if sys.platform != "darwin":
        print("[make_dmg] DMG packaging is macOS-only. Skipping.")
        sys.exit(1)
    if not APP.exists():
        print(f"[make_dmg] {APP} not found — run `python -m tools.build_app` first.")
        sys.exit(1)

    version = _read_version()
    dmg_name = f"{branding.APP_NAME}-{version}.dmg"
    dmg_path = DIST / dmg_name

    # Build a staging directory containing exactly:
    #   <APP_NAME>.app    — the actual application
    #   Applications      — symlink to /Applications, so the user sees
    #                        an Applications folder icon and drags the
    #                        .app onto it.
    print(f"[make_dmg] staging in {STAGING}")
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    # copytree with symlinks=True preserves the .app's internal symlinks
    # (PyInstaller bundles contain links into Python.framework/Versions/)
    shutil.copytree(APP, STAGING / f"{branding.APP_NAME}.app", symlinks=True)
    (STAGING / "Applications").symlink_to("/Applications")

    # Replace any old DMG so hdiutil doesn't refuse to overwrite.
    if dmg_path.exists():
        dmg_path.unlink()

    # hdiutil create with ULFO = LZFSE-compressed read-only DMG. About
    # 12% smaller than the older UDZO (zlib) format on our payload, which
    # lets the Tester-bundled DMG stay under GitHub's 100MB single-file
    # ceiling. ULFO is supported on macOS 10.11+ — older than any Mac
    # we'd realistically distribute to.
    # `-fs HFS+` matters here: without it hdiutil defaults to APFS on
    # modern macOS, which adds ~8 MB of snapshot/metadata overhead on a
    # 100MB payload. HFS+ keeps the DMG comfortably under GitHub's 100MB
    # single-file ceiling once Tester is bundled.
    print(f"[make_dmg] creating {dmg_path}")
    subprocess.check_call([
        "hdiutil", "create",
        "-volname", branding.APP_NAME,
        "-srcfolder", str(STAGING),
        "-ov", "-format", "ULFO", "-fs", "HFS+",
        str(dmg_path),
    ])

    # Clean up staging — we don't need it after the DMG is built.
    shutil.rmtree(STAGING)

    size_mb = dmg_path.stat().st_size / (1024 * 1024)
    print(f"\n✓ Built: {dmg_path}  ({size_mb:.1f} MB)")
    print(f"  Distribute this single file. Users double-click → drag .app to Applications.")


if __name__ == "__main__":
    main()

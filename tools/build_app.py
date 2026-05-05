"""One-shot ClosetMind desktop bundler.

Usage:
    .venv/bin/python -m tools.build_app

Steps:
  1. Verify pyinstaller is installed; pip install if missing.
  2. Wipe previous dist/ and build/ directories.
  3. Invoke pyinstaller with closetmind.spec.
  4. Print where the .app / executable landed.

Design choices:
  - We delete dist/ and build/ before each run because PyInstaller
    occasionally caches incorrectly when .spec contents change, and the
    bundle is small enough (~30-60 sec rebuild) that always-clean is
    worth the predictability.
  - We don't sign the macOS .app here. Code signing requires an Apple
    Developer cert ($99/yr) and is orthogonal to the build itself —
    documented separately in BUILD.md if/when needed.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "closetmind.spec"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"


def _ensure_pyinstaller() -> None:
    """pip install pyinstaller into the active venv if not present."""
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("[build] PyInstaller not found — installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"])


def _clean() -> None:
    for d in (DIST_DIR, BUILD_DIR):
        if d.exists():
            print(f"[build] removing {d}")
            shutil.rmtree(d)


def _run_pyinstaller() -> None:
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC_FILE), "--noconfirm"]
    print(f"[build] running: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=str(PROJECT_ROOT))


def _report() -> None:
    if sys.platform == "darwin":
        app = DIST_DIR / "ClosetMind.app"
        if app.exists():
            print(f"\n✓ Built: {app}")
            print(f"  Test with:  open '{app}'")
            print(f"  Or:         '{app}/Contents/MacOS/ClosetMind'  (to see logs)")
            return
    folder = DIST_DIR / "ClosetMind"
    if folder.exists():
        ext = ".exe" if sys.platform == "win32" else ""
        print(f"\n✓ Built: {folder}/ClosetMind{ext}")
    else:
        print(f"\n✗ Expected output not found in {DIST_DIR}")


def main() -> None:
    _ensure_pyinstaller()
    _clean()
    _run_pyinstaller()
    _report()


if __name__ == "__main__":
    main()

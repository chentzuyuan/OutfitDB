# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ClosetMind desktop bundle.

Build with:
    pyinstaller closetmind.spec --noconfirm

Produces:
    dist/ClosetMind.app   (macOS)
    dist/ClosetMind/      (Linux/Windows folder bundle)

Three known PyInstaller-with-FastAPI-stack gotchas pre-handled here:

  1. Jinja2 templates / FastAPI StaticFiles look up files on disk via
     paths derived from `Path(__file__).parent`. PyInstaller stores
     pure-Python modules inside an archive, so __file__ doesn't point
     at a real on-disk directory. main.py was patched to fall back to
     `sys._MEIPASS` when frozen — and `datas` below copies the templates
     and static files to that location.

  2. PIL / Pillow loads image-format plugins via dynamic import
     (`__import__("PIL.JpegImagePlugin")`). PyInstaller's static analysis
     misses these. `collect_submodules('PIL')` pulls all of them in.

  3. xgboost ships a compiled native library (libxgboost.dylib on macOS,
     xgboost.dll on Windows) plus a VERSION marker file. PyInstaller
     misses both unless we use `collect_all('xgboost')`. Same idea for
     sklearn (which xgboost imports for metrics) — its tree, ensemble,
     and metrics modules use Cython extensions that need explicit
     collection.
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all
import os
import sys

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.dirname(SPEC))


# ─── 1. Templates + static files (Jinja2 / StaticFiles fix) ─────────────
datas = [
    ("app/static", "app/static"),
    ("app/templates", "app/templates"),
]

# ─── 1b. Seed Tester profile (so the .app has a working wardrobe on
#       first launch — wardrobe.db + items/images + models). The seed
#       lives outside the project root, so reference by absolute path
#       and ship it under seed_profiles/Tester inside the bundle.
TESTER_SRC = os.path.normpath(os.path.join(PROJECT_ROOT, "..", "profiles", "Tester"))
if os.path.isdir(TESTER_SRC):
    datas.append((TESTER_SRC, "seed_profiles/Tester"))

# ─── 2. Pillow plugins ──────────────────────────────────────────────────
hiddenimports = collect_submodules("PIL")

# ─── 3. xgboost + sklearn (compiled native libs + Cython extensions) ────
xgboost_datas, xgboost_binaries, xgboost_hidden = collect_all("xgboost")
datas += xgboost_datas
sklearn_datas, sklearn_binaries, sklearn_hidden = collect_all("sklearn")
datas += sklearn_datas
binaries = xgboost_binaries + sklearn_binaries
hiddenimports += xgboost_hidden + sklearn_hidden

# Uvicorn ASGI worker / lifespan / websocket protocols are dynamically
# imported based on config — pull in the full set so any worker class
# we might switch to in the future works.
hiddenimports += collect_submodules("uvicorn")

# Multipart parsing (file uploads via /items/upload) — fastapi imports
# it lazily; pin it here so PyInstaller doesn't drop it.
hiddenimports += [
    "multipart",
    "python_multipart",
    "email_validator",
    "passlib",
    "bcrypt",
]

# Pydantic v2 has compiled core (pydantic_core) — usually picked up,
# but safer to be explicit.
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_core")


a = Analysis(
    ["launcher.py"],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Skip large modules we don't actually use, to slim the bundle.
    excludes=[
        "matplotlib",  # only used by tools/training/window_sweep.py (dev tool)
        "tkinter",
        "test",
        "unittest",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClosetMind",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX often breaks Mac signing; skip
    console=True,         # keep terminal so user sees the URL on launch.
                          # flip to False for "silent" GUI app once stable.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ClosetMind",
)

# macOS .app bundle wrapper
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ClosetMind.app",
        icon=None,        # TODO: add app/static/icons/icon-512.png as .icns
        bundle_identifier="com.closetmind.app",
        version="0.1.0",
        info_plist={
            "CFBundleName": "ClosetMind",
            "CFBundleDisplayName": "ClosetMind",
            "CFBundleVersion": "0.1.0",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # Don't auto-show the Python window; we use the system browser
            # for the actual UI. The terminal output is for debugging.
            "LSBackgroundOnly": False,
        },
    )

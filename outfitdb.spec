# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OutfitDB desktop bundle.

Build with:
    pyinstaller outfitdb.spec --noconfirm

Produces:
    dist/OutfitDB.app   (macOS)
    dist/OutfitDB/      (Linux/Windows folder bundle)

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

# Read brand identity from app/branding.py so renaming the app is a
# one-file edit. We can't `import app.branding` directly because the
# spec file runs before pip's package install paths are guaranteed,
# so we exec the module text instead — same outcome, no sys.path mess.
_branding_ns = {}
with open(os.path.join(PROJECT_ROOT, "app", "branding.py"), encoding="utf-8") as _bf:
    exec(_bf.read(), _branding_ns)
APP_NAME = _branding_ns["APP_NAME"]
APP_BUNDLE_ID = _branding_ns["APP_BUNDLE_ID"]

# App version comes from app/version.py (separate file so the spec
# doesn't need to update on every release).
_version_ns = {}
with open(os.path.join(PROJECT_ROOT, "app", "version.py"), encoding="utf-8") as _vf:
    # version.py imports branding via `from . import branding` which
    # fails when exec'd in isolation — strip that line to keep this
    # bootstrap simple. We only need APP_VERSION here.
    _src = _vf.read()
    _src = "\n".join(line for line in _src.splitlines() if line.strip() != "from . import branding")
    # Stub out branding usages we don't need at spec time.
    _src = _src.replace("branding.resolve_env(\"UPDATE_FEED\")", "None")
    _src = _src.replace("branding.DEFAULT_UPDATE_FEED", "''")
    _src = _src.replace("branding.APP_NAME", repr(APP_NAME))
    exec(_src, _version_ns)
APP_VERSION = _version_ns["APP_VERSION"]


# ─── 1. Templates + static files (Jinja2 / StaticFiles fix) ─────────────
datas = [
    ("app/static", "app/static"),
    ("app/templates", "app/templates"),
]

# ─── 1b. Seed Tester profile (so the .app has a working wardrobe on
#       first launch — wardrobe.db + items/images + models).
#
# Look at two candidate source paths so the same .spec works in two
# environments without a build-time tweak:
#
#   1. PROJECT_ROOT/seed_profiles/Tester   ← canonical committed copy
#                                            (works on GitHub Actions
#                                            Windows runner, fresh clones)
#   2. PROJECT_ROOT/../profiles/Tester     ← dev sibling layout where the
#                                            real wardrobe lives next to
#                                            this repo (so my local Mac
#                                            doesn't need a duplicate).
#
# First path that resolves to an actual directory wins. If neither
# exists the bundle still builds, but seed_default_profile_if_empty()
# in app/config.py won't have anything to copy on first launch.
TESTER_CANDIDATES = [
    os.path.join(PROJECT_ROOT, "seed_profiles", "Tester"),
    os.path.normpath(os.path.join(PROJECT_ROOT, "..", "profiles", "Tester")),
]
for candidate in TESTER_CANDIDATES:
    if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "wardrobe.db")):
        datas.append((candidate, "seed_profiles/Tester"))
        print(f"[spec] Bundling Tester seed from {candidate}")
        break
else:
    print("[spec] WARNING: no Tester seed found at any candidate path")

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
    name=APP_NAME,
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
    name=APP_NAME,
)

# macOS .app bundle wrapper
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,        # TODO: add app/static/icons/icon-512.png as .icns
        bundle_identifier=APP_BUNDLE_ID,
        version=APP_VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleVersion": APP_VERSION,
            "CFBundleShortVersionString": APP_VERSION,
            "NSHighResolutionCapable": True,
            # Don't auto-show the Python window; we use the system browser
            # for the actual UI. The terminal output is for debugging.
            "LSBackgroundOnly": False,
        },
    )

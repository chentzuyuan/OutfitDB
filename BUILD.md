# Building OutfitDB as a desktop app

OutfitDB ships as a downloadable `.app` (macOS) / `.exe` (Windows) /
`.AppImage` (Linux) so end users can double-click and run it without
installing Python. The app embeds the full FastAPI backend and opens
the user's default browser pointing at `http://127.0.0.1:<port>` —
the same UI you see during development, but bundled.

This document covers how to produce that bundle.

> **Pre-0.2.0 history.** OutfitDB was previously called *ClosetMind*.
> The on-disk repo path is still `wardrobe_env/closetmind/` for git
> history stability, and a few internal references (legacy migration
> paths, file names like `closetmind-releases`) remain on the old name
> for back-compat. User-facing branding is OutfitDB everywhere.

## Quick start

Two-step build for distribution:

```bash
cd closetmind
.venv/bin/python -m tools.build_app    # produces dist/OutfitDB.app  (~172 MB)
.venv/bin/python -m tools.make_dmg     # produces dist/OutfitDB-0.2.0.dmg (~83 MB compressed)
```

**For development testing** (just want to launch the .app yourself):
just run `tools.build_app` and double-click `dist/OutfitDB.app`. The
first run shows the `/setup` page asking for a profile name. After that
the app remembers and goes straight into the closet.

**For distribution** (giving the app to other people): run both commands
and ship the `.dmg`. End-user experience:

1. Download `OutfitDB-0.2.0.dmg`.
2. Double-click the DMG → window opens showing **OutfitDB.app** next
   to an **Applications** folder shortcut.
3. Drag OutfitDB.app onto Applications. Installed.
4. Launch from Spotlight (Cmd+Space → "OutfitDB"), Launchpad, or
   Finder → Applications.
5. **First launch only**: macOS Gatekeeper blocks unsigned apps with
   "developer cannot be verified". Tell users to **right-click the app
   in Applications → Open → confirm**. Subsequent launches work
   normally. (See "macOS Gatekeeper" section below for the long-term
   fix via Apple Developer signing.)

The DMG itself is read-only and self-contained. After installing, the
user can delete the DMG file with no consequence.

## What the build does

`tools/build_app.py` is a thin wrapper around PyInstaller that:

1. Installs `pyinstaller>=6.0` if it's not in the venv.
2. Wipes `dist/` and `build/` for a predictable clean build.
3. Runs `pyinstaller outfitdb.spec --noconfirm`.

The actual bundling logic is in `outfitdb.spec`. It pre-handles three
gotchas that always bite FastAPI-stack apps the first time you freeze
them:

| Gotcha | How `outfitdb.spec` handles it |
|---|---|
| Jinja2 / StaticFiles paths break in frozen mode because `__file__` points into the archive | `app/main.py` falls back to `sys._MEIPASS / "app"` when frozen; spec's `datas` copies `app/static/` and `app/templates/` to that path |
| PIL plugins (JPEG, PNG, etc.) loaded via dynamic import — PyInstaller's static analysis misses them | `collect_submodules("PIL")` |
| xgboost ships a compiled native lib + VERSION file; sklearn has Cython extensions | `collect_all("xgboost")` + `collect_all("sklearn")` |

## Versioning

`app/version.py` holds `APP_VERSION = "0.2.0"`. Bump it when releasing
a new build. The footer on every page shows this version.

## Auto-update mechanism

The app checks `https://raw.githubusercontent.com/buttegg/outfitdb-releases/main/latest.json`
on each page load (server-side, cached for 6 hours). If the manifest's
`version` is newer than the current `APP_VERSION`, the footer shows a
"New version available →" link pointing at the manifest's `url`. The
old `closetmind-releases` URL is still honoured if the new env var
isn't set, so manifests published before the rename keep working.

Manifest format:
```json
{
  "version": "0.3.0",
  "url": "https://github.com/buttegg/outfitdb-releases/releases/download/v0.3.0/OutfitDB.dmg",
  "notes": "Adds X / fixes Y"
}
```

To release a new version:
1. Bump `APP_VERSION` in `app/version.py`.
2. Run `python -m tools.build_app`.
3. Distribute `dist/OutfitDB.app` (zip it for transport — macOS extended
   attributes preserved by `ditto -c -k --keepParent`).
4. Update `latest.json` in the public releases repo.

The releases repo is **separate** from the source repo so the source can
stay private. Only the manifest + binary downloads are exposed.

To override the update feed (e.g., for testing), set
`OUTFITDB_UPDATE_FEED` to a different URL before launching. The legacy
`CLOSETMIND_UPDATE_FEED` is still read as a fallback.

## macOS Gatekeeper

Unsigned `.app` bundles are blocked by macOS Gatekeeper on download.
End users see an "OutfitDB cannot be opened because the developer
cannot be verified" dialog.

Workarounds:
- **For yourself / testers**: right-click the app → Open → confirm.
  Only needed once per machine.
- **For real distribution**: code-sign + notarize. Requires an Apple
  Developer Program membership ($99/yr). Out of scope for this build.

## Bundle size

Expect ~150-250 MB for the macOS `.app`. The bulk is xgboost + numpy +
Pillow + Python runtime. No good way to slim further without cutting
features (xgboost is the recommendation engine, numpy/Pillow are
unavoidable).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'X'` at runtime | Add `X` to `hiddenimports` in `outfitdb.spec` |
| Templates render `{{ … }}` literals in browser | `app/templates/` not copied to bundle — verify the `datas` line in the spec |
| Image upload fails with PIL error | Add `collect_submodules('PIL')` (already done — but verify the spec didn't get edited) |
| `xgboost.core.XGBoostError: ... library not found` | `collect_all("xgboost")` was missed — verify the spec |
| Port already in use | Launcher tries 18888-18999 first, then random; check if another instance is running |
| `dist/OutfitDB.app` won't open (Gatekeeper) | Right-click → Open the first time |

## Build prerequisites

- Python 3.12 in a venv at `closetmind/.venv/`
- `pip install -r requirements.txt` already done
- macOS: Xcode Command Line Tools (`xcode-select --install`)
- Windows: nothing special, builds clean from the venv

## Releasing checklist

- [ ] Bump `APP_VERSION` in `app/version.py`
- [ ] `python -m tools.build_app` — produces `dist/OutfitDB.app`
- [ ] Test the bundle launches and `/setup` works on a fresh data dir
- [ ] Test on a clean profile (`mv ~/.outfitdb ~/.outfitdb.bak`, then re-launch)
- [ ] `python -m tools.make_dmg` — produces `dist/OutfitDB-X.Y.Z.dmg`
- [ ] Test the DMG: double-click → mount → drag .app to Applications shortcut → launch from Applications
- [ ] Upload to releases repo, update `latest.json`
- [ ] Tag in source repo: `git tag vX.Y.Z && git push --tags`

# Renaming the app

The brand identity (the human-readable name, every "OutfitDB" the user
sees, the macOS bundle id, the localStorage key prefix, the path under
`~/Library/Application Support/`, etc.) is centralised in
[`app/branding.py`](app/branding.py). Every Python module, Jinja
template, and JS file reads from there at runtime, so renaming the app
is mostly editing one file.

This document covers the few things that *aren't* runtime — disk
artefacts, external services, GitHub repo settings — that still need a
manual touch when you rename.

## Code-side rename (the easy part)

Edit `app/branding.py`. Two kinds of edit:

1. **Promote the new name to canonical**: change `APP_NAME`,
   `APP_NAME_LOWER`, `APP_BUNDLE_ID`, `JS_NAMESPACE`, `LS_PREFIX`,
   `EVENT_PREFIX`, `USER_STATE_DIRNAME`, `APP_DATA_DIRNAME`,
   `DEFAULT_DATA_DIR_NAME`, `STYLE_PACK_FORMAT`, etc. as appropriate.
2. **Demote the old name to legacy**: prepend the previous values to
   `LEGACY_NAMES`, `LEGACY_USER_STATE_DIRNAMES`,
   `LEGACY_APP_DATA_DIRNAMES`, `LEGACY_LS_PREFIXES`,
   `LEGACY_EVENT_PREFIXES`, `LEGACY_BUNDLE_IDS`,
   `LEGACY_ENV_PREFIXES`. The legacy lists are walked on first launch
   under the new name so existing users keep their wardrobe + active
   profile + °F preference + skipped-checklist state without manual
   migration.

That's it. After editing branding.py, every page render produces the
new brand string; every pyinstaller build outputs the new .app /
.exe / DMG name; every new user gets the new directory layout.

## On-disk artefacts that change with the brand

These get the new name automatically on the next operation listed:

| Path / artefact | Refreshed by |
|---|---|
| `~/<USER_STATE_DIRNAME>/active` | First server import (legacy mirrored on first launch) |
| `~/Library/Application Support/<APP_DATA_DIRNAME>/profiles/` | First frozen-app launch |
| `dist/<APP_NAME>.app` / `dist/<APP_NAME>.exe` | `python -m tools.build_app` |
| `dist/<APP_NAME>-{version}.dmg` | `python -m tools.make_dmg` |
| `dist/<APP_NAME>-{version}-windows.zip` | `build-windows.yml` workflow |
| Settings page + Home hero + every <title> | Next request — they read `{{ brand.app_name }}` |
| `window.OD_BRAND` injection in base.html | Next request |
| i18n strings containing `__BRAND__` | Same |
| Service-worker cache key | Bump `VERSION` in `app/static/js/sw.js` so installed PWAs drop the old cache |

## Things still hardcoded (and why)

A handful of identifiers are intentionally left as static strings:

- **Repo directory name on disk** (`wardrobe_env/closetmind/`). Renaming
  the dir would invalidate every absolute path that anyone has stored
  (git remotes, local bookmarks, Render's clone URL). The user-facing
  brand can drift away from the repo dir name without harm.
- **CSS class prefixes** (`.od-toast`, `.od-footer`, `.od-hero`,
  etc.). These aren't user-visible. Renaming them across CSS + every
  template that references them is busy-work for zero user benefit.
  Treat them like any other internal identifier.
- **Existing `latest.json` releases manifest URL**. The
  `branding.DEFAULT_UPDATE_FEED` is constructed from `APP_NAME_LOWER`
  so it tracks renames automatically. But the *previous* feed URL
  remains live (so already-installed users keep getting update
  notifications) until you decommission it; copy `latest.json` to the
  new feed location before deleting the old one.
- **GitHub repo + Render service names**. These are external services
  with their own rename procedures (see below).

## External rename steps (manual, web UI only)

1. **GitHub repo**: Settings → repo name → rename. GitHub auto-
   redirects the old URL for a while; readme badges + git remotes
   keep working without code changes.
2. **Render service**: Render dashboard → service → Settings → Name →
   change. Old `<old>.onrender.com` URL becomes 404 immediately
   (Render does *not* auto-redirect). Mitigation: pin the old
   subdomain as a custom-domain alias on the renamed service, or
   accept the break.
3. **Releases repo** (`<APP_NAME_LOWER>-releases` on GitHub holding
   `latest.json` + DMG/ZIP assets): create a fresh repo under the new
   name; copy current `latest.json` over; new releases publish here
   from now on. Leave the old releases repo intact until you're
   confident no v0.x users are still on the old update feed.
4. **GitHub repo About sidebar** (description, homepage, topics):
   edit `.github/repo-metadata.json` then run
   `.github/sync-repo-metadata.sh`.

## Style-pack format migration across renames

The style-pack format identifier is `branding.STYLE_PACK_FORMAT`
(`outfitdb-style-pack` today). On a rename you have two choices:

- **Keep the format identifier** — sharing across rename boundaries
  Just Works. The pack manifest still says `outfitdb-style-pack`
  forever, even though the app is no longer called OutfitDB. Choose
  this if you want backwards compatibility with packs already in
  circulation.
- **Bump the identifier** — packs from the old name cease to validate.
  Choose this only if the format itself materially changed at the
  same time (i.e. you're also bumping `STYLE_PACK_FORMAT_VERSION`).

The default is to keep the identifier. If you do change it, make the
import-side validator accept the previous identifier as a synonym so
existing packs keep loading.

## Sanity-check after a rename

Run this checklist before tagging:

```bash
# 1. Server boots
.venv/bin/uvicorn app.main:app --port 18890 --log-level warning &
sleep 2
curl -s http://127.0.0.1:18890/healthz
curl -s http://127.0.0.1:18890/version

# 2. HTML home contains only the new brand
curl -s http://127.0.0.1:18890/ | grep -o "<old-name>\|<new-name>" | sort -u

# 3. window.OD_BRAND is injected with the new identifiers
curl -s http://127.0.0.1:18890/ | grep -A1 "OD_BRAND"

# 4. PWA manifest renamed
curl -s http://127.0.0.1:18890/static/manifest.json | head

# 5. Style-pack format endpoint reports the right identifier
curl -s http://127.0.0.1:18890/style/format

# 6. Round-trip a pack
curl -s -o /tmp/pack.odstyle http://127.0.0.1:18890/style/export
curl -s -X POST http://127.0.0.1:18890/style/import -F "file=@/tmp/pack.odstyle"

kill %1
```

# OutfitDB — LLM primer

Self-contained briefing for any LLM (GPT, Claude, etc.) that needs to
understand this project from scratch. Paste the whole file into a fresh
chat and the model should be able to reason about architecture, answer
specific questions, and propose changes that fit the design.

---

## TL;DR

OutfitDB is a **local-first desktop app** that learns one user's
clothing preferences across three independent axes (temperature
appropriateness, aesthetic preference, occasion fit) and recommends
outfits from their own closet — including combinations the user would
never try themselves.

- **Stack**: FastAPI + SQLAlchemy 2.x + SQLite + XGBoost + Jinja2 + vanilla JS + PWA service worker + PyInstaller
- **Distribution**: macOS `.app` (DMG), Windows `.exe` (ZIP), Render free-tier demo
- **Privacy posture**: no cloud, no account, no telemetry; user's data stays in `~/Library/Application Support/OutfitDB/profiles/<name>/`
- **Current version**: v0.3.0 (May 2026)
- **Current state**: working end-to-end; v0.3.0 just shipped style-pack export/import foundation
- **Active repo**: github.com/chentzuyuan/OutfitDB (renamed from `closetmind` May 2026)
- **Live demo**: outfitdb.onrender.com (also closetmind.onrender.com still alive)

---

## Why it exists

"What should I wear today?" combines three judgments that resist a
closed-form answer — *is this warm enough for the weather, is it
appropriate for the occasion, do I actually like how it looks* — and
each judgment is personal. OutfitDB demonstrates that with a small
relational schema for the user's wardrobe, three short rating flows,
and a classifier per axis, the problem becomes tractable on a laptop.

It's deliberately *teach-by-doing*: a 13-table SQLite schema, a
three-stage XGBoost pipeline, a hand-coded layer-coverage thermal
model, and an auto-update flow are all in one repo small enough to
read end-to-end. The author's broader pitch is "anyone can build their
own database and train their own ML model for everyday problems that
don't have a tidy mathematical answer" — OutfitDB is the first of a
planned series.

---

## The recommendation engine

```
   ┌─────────────────────────┐
   │ Layer Coverage Model    │  hand-coded gates (no learning)
   │  - warmth in [0.6×,1.5×]│
   │  - formality floor      │
   │  - no orphan outerwear  │
   └────────────┬────────────┘
                │  pruned candidate pool
                ▼
   ┌──────────┐ × ┌──────────┐ × ┌──────────┐
   │  Stage 1 │   │  Stage 2 │   │  Stage 3 │     three independent
   │   Temp   │   │ Aesthetic│   │ Occasion │     XGBoost classifiers
   └──────────┘   └──────────┘   └──────────┘
        prob × prob × prob × freshness × recovery
                          │
                          ▼
                       Top-K outfits
```

### Layer Coverage Model (hard gate)

Each outfit has a **temperature curve** `coverage_curve = [C₁ … C₄]`
where `Cₖ` is the cumulative warmth of the outermost `k` layers,
mapped to a "minimum comfortable temperature":

```
T_cover(k) = 30 − 2.5 × Cₖ    (in °C)
```

Three hard rules at candidate generation prune the search space
before any classifier sees a candidate:

1. Total warmth ∈ [0.6×, 1.5×] × ideal warmth for the temperature
2. Average core formality ≥ floor(occasion)
3. No orphan outerwear without an underlayer (governed by per-item `can_wear_alone`)

Code: `app/services/outfit_generator.py`.

### Three XGBoost classifiers

Each gate is independently learnable from its own pure-axis training
UI, so "great look but wrong temperature" never cross-contaminates
the other signals.

| Stage | UI route                  | Storage table          | Model file                | Format                 |
|-------|---------------------------|------------------------|---------------------------|------------------------|
| 1     | `/training/temperature`   | `temperature_ratings`  | `models/stage1_temp.json` | multi-label (6 zones) |
| 2     | `/training/aesthetic`     | `ratings`              | `models/current.json`     | 4-point scale         |
| 3     | `/training/occasion`      | `occasion_ratings`     | `models/stage3_occasion.json` | multi-label (9 events) |

Inference: each classifier produces a probability; the three are
multiplied, then multiplied with two soft factors (`freshness`,
`recovery`) so the final score balances pure-axis judgement with
discovery (surfacing neglected items).

Code:
- `app/services/feature_engineering.py` (100-d feature vector,
  `FEATURE_VERSION = 1`)
- `app/services/scoring.py` (the multiplication chain)
- `app/services/model_training.py` (stage 2)
- `app/services/multi_stage_training.py` (stages 1 + 3)

### Why three stages instead of one big model

Earlier prototype used a single classifier. Problem: a beautiful
outfit that's freezing in winter would get a high score and crowd
out warm-but-plain alternatives. Splitting axes lets each gate fail
independently — wrong-temperature outfits drop to score 0 regardless
of how aesthetically pleasing they are.

---

## Data model (13 tables)

In `app/models.py`. SQLAlchemy 2.x ORM, single SQLite file per
profile (`wardrobe.db`).

| Table | Purpose |
|-------|---------|
| `users`               | one row per profile (the active user). Holds location/timezone, temp-unit preference, has_thermal_insoles toggle |
| `items`               | every garment — name, category, colors[], material, composition[], thickness, pattern, layer_role, can_wear_alone, fit/length/collar attrs |
| `item_tags`           | many-to-many style tags |
| `item_state`          | per-item lifecycle: clean / worn / in_laundry / unavailable. Separated from `items` so high-write state changes don't bloat the read-heavy main table |
| `item_stats`          | aggregated per-item stats (wear count, last worn, ratings histogram) |
| `daily_contexts`      | one row per day-with-recommendation: date, temperature, weather, occasion, calendar_event |
| `outfits`             | a generated/manual outfit. Holds aggregated `warmth_score`, `coverage_curve`, formality average |
| `outfit_items`        | many-to-many outfit ↔ item |
| `ratings`             | aesthetic ratings (-1 / 0 / 1 / 2) — feeds Stage 2 |
| `temperature_ratings` | which zones an outfit suits — feeds Stage 1 |
| `occasion_ratings`    | which events an outfit fits — feeds Stage 3 |
| `outfit_logs`         | actual wear history (was-worn-on-date, weather actually experienced) |
| `model_runs`          | training-run audit trail (timestamp, val AUC, sample count) |

Migrations are **idempotent ALTER TABLEs** in `app/database.py`
(`USERS_MIGRATIONS`, etc.) — every startup walks them and adds any
missing column. No Alembic.

JSON columns: `items.colors`, `items.style_tags`, `items.composition`
(weighted multi-hot for fabric blends, e.g. `[{"material":"cotton","pct":40},{"material":"linen","pct":60}]`).

### 9 enums

In the same file: `CategoryEnum` (top/bottom/shoes/accessory/fullbody),
`LayerRoleEnum`, `ThicknessEnum`, `ItemStateEnum`, `UserActionEnum`
(accepted/modified/rejected), `RatingSourceEnum`, `WeatherEnum`,
`OccasionEnum`, plus 8 zone categories used in temperature_ratings.

---

## Local-first design

User data **never** crosses the network. Three modes:

1. **Dev mode** (running from source via `uvicorn app.main:app`):
   profiles live in `<repo>/profiles/<name>/`
2. **Frozen-app mode** (downloaded `.app`/`.exe`):
   profiles live in OS-standard application data:
   - macOS: `~/Library/Application Support/OutfitDB/profiles/`
   - Windows: `%APPDATA%\OutfitDB\profiles\`
   - Linux: `$XDG_DATA_HOME/OutfitDB/profiles/`
3. **Render demo mode** (env var `OUTFITDB_RENDER_MODE=1`):
   profiles live in `/tmp/outfitdb/profiles/`. Each cold start re-seeds
   the bundled Tester profile so visitors get a clean wardrobe.

Per-profile structure:

```
<profile>/
├── wardrobe.db          # SQLite (all 13 tables)
├── items/images/        # uploaded clothing photos
├── models/
│   ├── current.json     # Stage 2 (aesthetic) XGBoost
│   ├── stage1_temp.json
│   ├── stage3_occasion.json
│   ├── archive/         # prior models, auto-rotated on retrain
│   └── imported/        # style packs imported from other users
└── README.txt
```

Per-user state pointer (which profile is active) lives in
`~/.outfitdb/active`. Free-tier Render and the local desktop install
share this layout.

The browser's Open-Meteo weather call goes from the *user's* IP, not
the server's — code in `app/static/js/weather.js`. So the Render
demo doesn't carry a per-visitor weather quota.

---

## Branding hook (`app/branding.py`)

Single source of truth for every brand-coupled string. The module
exists because OutfitDB was renamed from ClosetMind in May 2026 (see
"Naming history" below) and the lessons from that pain are now
encoded so future renames are a one-file edit.

What lives in `branding.py`:

```python
APP_NAME = "OutfitDB"            # display name
APP_NAME_LOWER = "outfitdb"      # filesystem / URL slug
APP_BUNDLE_ID = "com.outfitdb.app"
APP_DATA_DIRNAME = APP_NAME      # the OS-standard app-data folder
USER_STATE_DIRNAME = ".outfitdb" # ~/.outfitdb
JS_NAMESPACE = "od"              # window.od.*
LS_PREFIX = "od_"                # localStorage keys (od_temp_unit, od_lang, ...)
EVENT_PREFIX = "od-"             # custom events (od-temp-unit-change)
STYLE_PACK_EXTENSION = ".odstyle"
STYLE_PACK_FORMAT = "outfitdb-style-pack"
STYLE_PACK_FORMAT_VERSION = 1

LEGACY_NAMES = ["ClosetMind"]
LEGACY_USER_STATE_DIRNAMES = [".closetmind"]
LEGACY_APP_DATA_DIRNAMES = ["ClosetMind"]
LEGACY_LS_PREFIXES = ["cm_"]
LEGACY_EVENT_PREFIXES = ["cm-"]
LEGACY_BUNDLE_IDS = ["com.closetmind.app"]
LEGACY_ENV_PREFIXES = ["CLOSETMIND_"]
```

How it's wired:

| Surface | How it reads brand |
|---------|---------------------|
| Python | `from app import branding; branding.APP_NAME` |
| Jinja templates | `{{ brand.app_name }}` (injected via `templates.env.globals`) |
| JS | `window.OD_BRAND.appName` (injected by base.html as `<script>window.OD_BRAND = {{ brand_js_json|safe }}</script>` BEFORE i18n.js / temp_unit.js load) |
| i18n strings | translations use literal token `__BRAND__`; `t()` and `applyTranslations()` substitute it from `OD_BRAND.appName` at render time |
| `<title>` | each page declares `{% block title_key %}page.title.<x>{% endblock %}`; `applyTranslations()` writes through `document.title` on language change |
| PyInstaller spec | `outfitdb.spec` does `exec(open("app/branding.py").read())` to read constants without sys.path setup |

Legacy migration: on first launch under a new brand, `app/config.py`
walks `LEGACY_USER_STATE_DIRNAMES` + `LEGACY_APP_DATA_DIRNAMES` and
mirror-copies the data into the new locations. Multiple renames stack
transitively — a user upgrading through ClosetMind → OutfitDB → "FooBar"
keeps their wardrobe across all three.

The on-disk repo dir is intentionally `closetapp/` (brand-neutral) so
a future rename doesn't require shuffling the dir + rebuilding venv.

Doc: `BRANDING.md` walks the rename procedure end-to-end.

---

## Style packs (`.odstyle`)

Cross-platform format for sharing trained preference models between
users. Foundation shipped in v0.3.0; activation UI ("use Bruce's
style") is the next planned milestone.

### Format spec

A `.odstyle` file is a ZIP archive (Python `zipfile` produces
byte-identical output on macOS / Linux / Windows, so packs are
trivially portable). Contents:

```
manifest.json         # see below
models/aesthetic.json # Stage 2 XGBoost dump
models/temperature.json # Stage 1, optional
models/occasion.json    # Stage 3, optional
```

Manifest:

```json
{
  "format": "outfitdb-style-pack",
  "format_version": 1,
  "feature_version": 1,
  "exported_by": {
    "app": "OutfitDB",
    "app_version": "0.3.0",
    "profile": "Bruce"
  },
  "exported_at": "2026-05-06T05:30:00Z",
  "axes": ["aesthetic", "temperature", "occasion"],
  "model_files": {
    "aesthetic": "models/aesthetic.json",
    "temperature": "models/temperature.json",
    "occasion": "models/occasion.json"
  },
  "stats": { "n_items": 54, "n_ratings": 753 }
}
```

### Importer rules (gates that reject)

- `format` ≠ `branding.STYLE_PACK_FORMAT` → reject (different app)
- `format_version` > current → reject (newer-than-importer)
- `feature_version` ≠ `feature_engineering.FEATURE_VERSION` → reject
  (incompatible feature pipeline; would silently score wrong)

Imported packs land in `<profile>/models/imported/{author_safe}-{date}/`
**alongside** (not over) the user's own model. Activation is
explicit and not yet implemented.

### Endpoints (`app/routers/style.py`)

- `GET /style/export` → downloads `<APP_NAME>-<profile>-style-<date>.odstyle`
- `POST /style/import` → multipart upload of a `.odstyle` file
- `GET /style/imports` → list previously-imported packs
- `DELETE /style/imports/{id}` → remove an import
- `GET /style/format` → tells the client what format version this build understands

Code: `app/services/style_pack.py` (format spec + export + import +
validation), `app/routers/style.py` (HTTP glue).

---

## Codebase layout

```
closetapp/                          # on-disk dir (brand-neutral)
├── app/
│   ├── branding.py                 # single source of truth (see above)
│   ├── version.py                  # APP_VERSION + cached remote-update check
│   ├── config.py                   # profile / data-dir resolution + legacy migration
│   ├── database.py                 # SQLAlchemy engine + idempotent migrations
│   ├── models.py                   # 13 ORM tables, 9 enums
│   ├── crud.py
│   ├── schemas.py                  # Pydantic
│   ├── main.py                     # FastAPI app + lifespan + page routes
│   ├── routers/                    # 13 API routers (see below)
│   ├── services/                   # business logic (see below)
│   ├── templates/                  # Jinja2 HTML (11 pages)
│   └── static/                     # CSS, JS (i18n, temp_unit, weather, sw), icons
├── seed_profiles/Tester/           # bundled sample wardrobe (54 items, all axes trained)
├── tools/
│   ├── build_app.py                # PyInstaller wrapper
│   ├── make_dmg.py                 # macOS DMG packager
│   ├── make_icons.py               # PWA icon generation
│   ├── migrate_to_local_first.py   # one-shot Phase 3 legacy migration
│   ├── training/                   # post-setup training utilities (SHAP, demos, sweep)
│   ├── demo_data/                  # Tester wardrobe bootstrap (specs + importers)
│   └── audit/                      # MD5-dup + color-mismatch scanners
├── seeder/                         # legacy demo seeder (Phase 1 era)
├── outfitdb.spec                   # PyInstaller spec
├── launcher.py                     # frozen-app entry point (uvicorn + browser open)
├── render.yaml                     # Render deploy config
├── requirements.txt
├── README.md, BUILD.md, BRANDING.md
└── .github/
    ├── workflows/
    │   ├── build-windows.yml       # tag-triggered, PyInstaller on Windows runner
    │   └── upload-staged-dmg.yml   # push to release-staging-<tag> branch → release
    ├── repo-metadata.json          # versioned About-sidebar metadata
    └── sync-repo-metadata.sh       # one-shot pusher (gh api PATCH)
```

### Routers (`app/routers/*.py`)

| Path | Purpose |
|------|---------|
| `setup.py`           | first-run /setup wizard |
| `profiles.py`        | multi-profile switcher |
| `settings.py`        | /users/settings — location, temp_unit, thermal-insoles toggle |
| `items.py`           | CRUD + `/items/upload` (multipart image + attrs) + CSV bulk import |
| `contexts.py`        | `/contexts/` — daily weather + occasion (server doesn't fetch weather; browser does) |
| `outfits.py`         | manual outfit CRUD |
| `ratings.py`         | direct -1/0/1/2 outfit rating |
| `recommendations.py` | `/recommendations/` Top-K + `/recommendations/log` user_action |
| `train.py`           | manual stage-2 retrain trigger |
| `training.py`        | legacy 6-batch calibration UI (Phase 1 era) |
| `training_v2.py`     | the three pure-axis training UIs (Phase 4) |
| `stats.py`           | aggregations for `/stats/home` checklist |
| `style.py`           | export/import .odstyle (Phase 5) |

### Services (`app/services/*.py`)

| Path | Purpose |
|------|---------|
| `outfit_generator.py`     | candidate generation + 3 hard gates |
| `feature_engineering.py`  | 100-d feature vector, `FEATURE_VERSION = 1` |
| `scoring.py`              | DailySuitabilityScore (3-stage chain) + layering hints |
| `model_training.py`       | stage 2 (aesthetic) XGBoost trainer |
| `multi_stage_training.py` | stages 1 + 3 + inference |
| `weather_context.py`      | `WeatherEnum` mapping |
| `style_pack.py`           | export/import + manifest validation |

---

## Frontend conventions

- **Vanilla JS, no framework.** Each page is a Jinja template with an
  inline `<script>` block.
- **i18n**: `app/static/js/i18n.js` exports `window.t(key, vars)` +
  `window.applyTranslations()`. Every translatable element has
  `data-i18n="key"` or `data-i18n-attr="attr:key"`. The brand
  placeholder `__BRAND__` is substituted at render time. Two languages:
  `zh` (Traditional Chinese) and `en`.
- **Temperature unit**: `app/static/js/temp_unit.js` exposes
  `window.od.{tempUnit, tempUnitSymbol, fromCelsius, toCelsius,
  formatTemp, formatTempInt}`. DB always stores Celsius; this module
  is the only place that converts.
- **Service worker**: `app/static/js/sw.js`. Stale-while-revalidate
  for static assets, network-first for HTML, network-first for
  i18n.js + temp_unit.js (so new translation keys land immediately).
- **PWA**: `app/static/manifest.json`, installable on iOS Safari /
  Android Chrome.

---

## Versions & history

| Tag | Date | Theme |
|-----|------|-------|
| (Initial commit) | Apr 2026 | FastAPI + Tester demo seed for Render |
| v0.1.1 | May 4 | daily/nightly wording + cumulative UX fixes |
| v0.1.2 | May 5 | bundle a fully-trained Tester baseline + spec fix + issue triage |
| v0.1.3 | May 5 | layering hints + thermal-insoles toggle |
| v0.2.0 | May 6 | rename ClosetMind → OutfitDB |
| v0.3.0 | May 6 | branding hook + style-pack export/import foundation |

Deeper history: see `git log --oneline` (~30 commits) and the
six-phase narrative on the Quarto landing page (`outfitdb.qmd` in the
sibling `buttegggggggg.github.io` repo).

The six "phases" in the marketing docs:
- Phase 1 — Layer Coverage Model (thermal physics)
- Phase 2 — XGBoost preference (single classifier)
- Phase 3 — Local-first storage (per-profile SQLite)
- Phase 4 — Multi-stage training (three pure-axis classifiers)
- Phase 5 — Discovery polish (fabric blends, lifecycle states, force-anyway)
- Phase 6 — Desktop packaging (PyInstaller, DMG, auto-update)

---

## Naming history

The project was called **ClosetMind** until v0.2.0 (May 6, 2026).
Renamed to **OutfitDB** because:

1. A separate project named *ClosetMindAI* (a college pitch deck +
   coming-soon Instagram post) went live two days before this repo
   went public. Same use case (AI closet organizer), no shipped
   product, but the brand-name collision was real.
2. The author's longer-term plan involves Instagram marketing + a
   style-sharing community, which only works with a clean name.
3. "OutfitDB" is more honest about what the app actually is — a
   personal outfit database — and less twee than "ClosetMind".

The rename was non-trivial: ~28 files touched, +1407/-248 lines.
Lessons learned drove the design of `app/branding.py` (so the next
rename is one file).

The on-disk repo dir is `closetapp/` (brand-neutral) — chosen
deliberately so future renames don't require shuffling the dir +
rebuilding venv.

A redirect-tombstone repo lives at `chentzuyuan/OutfitDB_App`
(formerly `ClosetMind_App`) — it housed the v0.1.0 binaries when the
source repo was still private. README points everyone at the active
repo.

---

## What's deployed where

| Surface | URL / location | Status |
|---------|---------------|--------|
| Source repo | github.com/chentzuyuan/OutfitDB | active |
| v0.1.0 archive repo | github.com/chentzuyuan/OutfitDB_App | tombstone |
| Render demo (current) | outfitdb.onrender.com | v0.3.0 live |
| Render demo (legacy) | closetmind.onrender.com | v0.3.0 live (kept for old bookmarks) |
| macOS DMG | github.com/chentzuyuan/OutfitDB/releases/download/v0.3.0/OutfitDB-0.3.0.dmg | 95 MB |
| Windows ZIP | github.com/chentzuyuan/OutfitDB/releases/download/v0.3.0/OutfitDB-0.3.0-windows.zip | 237 MB |
| Quarto landing | outfitdb.html on chentzuyuan.github.io / buttegggggggg.github.io | v0.3.0 |
| Update feed | raw.githubusercontent.com/buttegg/outfitdb-releases/main/latest.json | manually maintained |

---

## Build & release pipeline

- **Windows ZIP**: GitHub Actions workflow `build-windows.yml` runs
  `pyinstaller outfitdb.spec` on `windows-latest` for every `v*` tag,
  attaches the resulting ZIP to the matching release.
- **macOS DMG**: must be built locally because GitHub doesn't have
  free macOS runners with code-signing. Procedure:
  ```bash
  python -m tools.build_app    # produces dist/OutfitDB.app
  python -m tools.make_dmg     # produces dist/OutfitDB-X.Y.Z.dmg
  # then push to release-staging-vX.Y.Z branch:
  git checkout -b release-staging-vX.Y.Z
  git add -f OutfitDB-X.Y.Z.dmg
  git commit -m "stage DMG"
  git push -u origin release-staging-vX.Y.Z
  # → upload-staged-dmg.yml attaches it to the release, deletes branch
  ```
- **Render**: auto-deploys both `outfitdb` and `closetmind` services
  on every push to `main`. Free tier sleeps after 15 min idle, ~50 s
  cold start.

Auto-update check: `app/version.py` does a cached (6 h TTL) GET against
`branding.DEFAULT_UPDATE_FEED`. If the manifest's `version` is newer
than `APP_VERSION`, the footer surfaces a "New version available →"
link.

---

## Open work

In rough priority order (current as of May 7, 2026):

**High value:**
1. **Style activation UI** — let users actually *use* an imported
   `.odstyle`. Three modes to design: (a) swap (use Bruce's model
   instead of yours), (b) blend (ensemble), (c) compare (side-by-side
   recommendations). Current state: foundation shipped in v0.3.0;
   imports land in `models/imported/` but the swap-current button
   doesn't exist yet.
2. **Set `closetmind.onrender.com` to manual deploy** — saves ~50%
   of free-tier build minutes.
3. **Notify Taylor (the only known active user) about v0.3.0**.

**Medium:**
4. Per-axis import (only borrow Bruce's aesthetic, not his cold tolerance)
5. Quarto site Source-and-documentation section rewrite
6. Heated-vest as a special outerwear tag
7. Auto-update banner CTA polish
8. README screenshots / GIF demo

**Long-term:**
9. Public style gallery (the Instagram-shareable angle)
10. Pre-trained example styles bundled with new installs
11. iOS / Android wrapper (Capacitor or Tauri)
12. Trademark registration if the project scales

**Tech debt:**
- `closetmind-releases` legacy update-feed URL still in `branding.py` as fallback — drop eventually
- Service-worker `VERSION` constant should auto-derive from `APP_VERSION` instead of manual bump
- `closetmind.onrender.com` itself is eventually retire-able

---

## How to navigate the codebase

If asked specific questions:

- **"How does X work in the recommendation engine?"** →
  `app/services/scoring.py`, then trace into `outfit_generator.py`
  (gates) and `multi_stage_training.py` (inference)
- **"What does table Y contain?"** → `app/models.py` (single file,
  ~250 lines)
- **"Where's the API for Z?"** → `app/routers/<z>.py` — names match
  closely (e.g., recommendations → recommendations.py)
- **"How is feature Z computed?"** → `app/services/feature_engineering.py`
- **"How does first-run setup work?"** → `app/routers/setup.py` +
  `app/templates/setup.html` (standalone page, doesn't extend base.html)
- **"What's the brand-rename procedure?"** → `BRANDING.md`
- **"What's the build pipeline?"** → `BUILD.md`
- **"What's the data layout on disk?"** → "Local-first design" section above
- **"What's the style-pack format?"** → "Style packs" section above

Code style: type hints throughout; docstrings on non-trivial
functions; comments explain *why* (not *what*) — that's an explicit
convention in the existing code. Match the surrounding module's
voice when adding code.

---

## Key files for an LLM to read first

If you only have time to skim five files before reasoning about the
project:

1. `app/branding.py` — what's brand-coupled and what isn't
2. `app/models.py` — full data model in one place
3. `app/services/scoring.py` — the recommendation chain
4. `app/services/feature_engineering.py` — what a "feature vector" means here
5. `app/services/style_pack.py` — the share format spec

These five give a complete mental model. Everything else is
ergonomics or glue.

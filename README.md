# ClosetMind

A local-first wardrobe + outfit recommender. Combines a hand-coded
**Layer Coverage Model** (温度層次 / 美感層次 dual curves) with a
**three-stage learned classifier chain** (temperature × occasion × aesthetic)
so recommendations adapt to *your* wardrobe, *your* climate, and *your* taste —
without sending any data off your machine.

```
warmth check  ─×─  occasion check  ─×─  aesthetic preference  ─→  Top K outfits
   (XGBoost)        (XGBoost)              (XGBoost)
```

Each gate is independently learnable from its own pure-axis training UI, so
"great look but wrong temperature" never gets cross-contaminated into either
signal.

---

## Requirements

- **Python 3.12+**
- **macOS, Linux, or Windows** (any OS that runs Python — tested on macOS 14)
- **~500 MB disk** for the venv after install (XGBoost + scikit-learn + numpy)
- **No GPU required** — XGBoost runs CPU `tree_method="hist"` and the dataset
  is tiny (~3000 ratings)

External services (all optional):
- **Open-Meteo** — auto-fills today's temperature/weather (no API key, called
  from the browser, your IP not the server's)
- **OpenAI / Anthropic API** — only needed for `tools/training/llm_judge_loop.py`
  if you want a third-party LLM to grade outfits during training; the rest
  of the system never makes outbound calls

---

## Install

```bash
# 1. Get the code
git clone <your-repo>/closetmind.git
# or unzip a release archive
cd closetmind

# 2. Create a fresh virtualenv
python3.12 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Start the server
uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000** in your browser. First-run will redirect
to `/setup` — pick a folder for your data (defaults to `~/MyClosetMind/`),
then start uploading clothes.

---

## First-time walkthrough (5 min)

1. **Setup screen** — accept default data folder; the app creates
   `~/MyClosetMind/{wardrobe.db, items/images/, models/, calendar/}`
2. **Upload clothes** at `/upload` — drag-drop up to 20 items at once,
   each row gets category / color / material / thickness selectors
3. **Train temperature** at `/training/temperature` — for each shown outfit,
   tick which temp zones it suits (`<0°C` to `>30°C`, multi-select). Aim for
   ~100 ratings spread across all 6 zones; the system adapts and probes your
   weak zones automatically
4. **Train aesthetic** at `/training/aesthetic` — rate ~50 outfits on a
   four-point scale (Dislike / Meh / Like / Love)
5. **Train occasion** at `/training/occasion` — for each outfit, tick which
   of 9 events it fits (home / gym / beach / casual / date_night / interview /
   office / business_meeting / formal_event)
6. **Get recommendations** at `/recommend` — pick today's date + occasion,
   the chain produces Top-K outfits ranked by
   `temp_pass × occasion_pass × aesthetic_pref`. Click "Don't like this"
   on any card to retrain the model live with that signal

---

## Demo data (optional)

The repo ships with a 54-item demo wardrobe spec under `tools/demo_data/`.
To populate the Tester profile with the same data used in the documentation:

```bash
# 1. Create the Tester profile via /setup or via API
curl -X POST http://127.0.0.1:8000/setup -H "Content-Type: application/json" \
     -d '{"name": "Tester", "data_dir": "~/MyClosetMind"}'

# 2. Generate placeholder images, import items, attach photos
python -m tools.demo_data.import_main_wardrobe
python -m tools.demo_data.import_sport_casual
python -m tools.demo_data.import_cold_formal
python -m tools.demo_data.import_tuxedo_split
python -m tools.demo_data.import_white_tee
```

Note: the import scripts use Pillow placeholders for images; replacing them
with real product photos is up to you (or the LLM-driven DALL-E flow we
used in development — see `docs/ClosetMind_Documentation.docx` §6).

---

## Project layout

```
closetmind/
├── README.md                    ← this file
├── requirements.txt             ← pip dependencies
├── render.yaml                  ← optional Render.com deploy config
│
├── app/                         ← FastAPI application
│   ├── main.py                  ← entry point + lifespan + routes
│   ├── config.py                ← profile / data-dir resolution
│   ├── database.py              ← SQLAlchemy engine + auto-migrations
│   ├── crud.py
│   ├── schemas.py
│   ├── models.py                ← 17 ORM tables, 9 enums
│   ├── routers/                 ← 14 API routers
│   │   ├── setup.py             ← /setup (first-run)
│   │   ├── profiles.py          ← /profiles (multi-user switcher)
│   │   ├── settings.py          ← /users/settings (location, prefs)
│   │   ├── items.py             ← /items (CRUD + image upload)
│   │   ├── contexts.py          ← /contexts (daily weather/occasion)
│   │   ├── outfits.py           ← /outfits (CRUD)
│   │   ├── recommendations.py   ← /recommendations (Top-K)
│   │   ├── ratings.py           ← /ratings (-1/0/1/2)
│   │   ├── train.py             ← /train (manual stage 2 retrain)
│   │   ├── training.py          ← /training (legacy 6-batch calibration)
│   │   ├── training_v2.py       ← /training/v2 (Phase 4 three-stage)
│   │   ├── stats.py
│   │   ├── calendar.py          ← .ics import
│   │   └── ...
│   ├── services/                ← business logic
│   │   ├── outfit_generator.py  ← candidate generation + 3 hard gates
│   │   ├── feature_engineering.py ← 100-d feature vectors
│   │   ├── scoring.py           ← DailySuitabilityScore (3-stage chain)
│   │   ├── model_training.py    ← stage 2 (aesthetic) XGBoost
│   │   ├── multi_stage_training.py ← stages 1 + 3 XGBoost + inference
│   │   └── calendar_reader.py   ← .ics parsing
│   ├── templates/               ← Jinja2 HTML
│   └── static/                  ← CSS / JS / icons / service worker
│
├── docs/
│   └── ClosetMind_Documentation.docx  ← 50-page architecture & ML doc
│
├── seeder/                      ← legacy demo seeder (Phase 1 era)
│
└── tools/                       ← post-install utilities
    ├── make_icons.py            ← regenerate PWA icons
    ├── migrate_to_local_first.py ← one-shot legacy migration
    ├── training/                ← post-setup training utilities
    │   ├── aesthetic_train.py   ← rubric-based bulk aesthetic ratings
    │   ├── llm_judge_loop.py    ← LLM-as-judge training loop
    │   ├── targeted_negatives.py ← anti-pattern -1 campaign
    │   ├── shap_analyze.py      ← SHAP feature importance
    │   └── demo_showcase.py     ← multi-context recommendation print
    ├── demo_data/               ← Tester profile bootstrap
    │   ├── spec_*.json          ← wardrobe specs (40 + 10 + 3 + 2 items)
    │   ├── import_*.py          ← API-based importers
    │   └── make_*.py            ← Pillow placeholder generators
    ├── audit/
    │   ├── image_audit.py       ← MD5-dup + color-mismatch scanner
    │   └── finalize_processed_images.py ← rebuild 800px thumbnails
    └── docs/
        └── add_phase4_doc_chapter.py
```

User data lives in a separate folder (default `~/MyClosetMind/`). It is
**never** placed inside this code repo — the local-first architecture is
deliberate so you can move / sync / back up your wardrobe independently of
the application.

---

## API quick reference

| Method | Path | Purpose |
|---|---|---|
| POST | `/setup` | First-run: create data folder + initial profile |
| GET / PUT | `/profiles/active` | Switch active profile |
| POST | `/items/upload` | Upload garment (multipart, image + attributes) |
| GET | `/items/` | List items |
| POST | `/contexts/` | Upsert daily context (date + temp + weather + occasion) |
| POST | `/recommendations/` | Get top-K outfits for a context |
| POST | `/recommendations/log` | Record user_action (accepted/modified/rejected) |
| POST | `/ratings/` | Direct outfit rating (-1/0/1/2) |
| POST | `/train/` | Retrain stage 2 (aesthetic) XGBoost |
| GET / POST | `/training/v2/temp/{progress, next, submit}` | Stage 1 (temperature) |
| GET / POST | `/training/v2/aesthetic/{progress, next, submit}` | Stage 2 (aesthetic) |
| GET / POST | `/training/v2/occasion/{progress, next, submit}` | Stage 3 (occasion) |
| POST | `/calendar/upload` | Upload .ics file |

Full Swagger UI: **http://127.0.0.1:8000/docs**

---

## Architecture in one paragraph

The Layer Coverage Model assigns each outfit a temperature curve
`coverage_curve = [C1..C4]` where `Ck` is the cumulative warmth of the
outermost `k` layers, mapped to a "minimum comfortable temperature"
`T_cover(k) = 30 − 2.5 × Ck`. Three hard rules at candidate generation
ensure outfits are physically valid: warmth in `[0.6×, 1.5×] × ideal`,
average core formality ≥ occasion threshold, no orphan outerwear without
underlayer (governed by per-item `can_wear_alone`). Within the filtered
candidate pool, three XGBoost classifiers — one per pure axis (temperature,
occasion, aesthetic) — score each candidate; their probabilities multiply
together into a final score. New training data flows into the appropriate
stage's table (`temperature_ratings`, `ratings`, `occasion_ratings`) and
auto-retrains its model in the background.

For the deep version, see **`docs/ClosetMind_Documentation.docx`** (19
chapters covering schema, ML pipeline, scoring math, frontend walkthrough,
and Phase 4 multi-stage training architecture).

---

## License & data privacy

- All wardrobe data, photos, and trained models live exclusively in your
  user folder (`~/MyClosetMind/` by default). Nothing is uploaded.
- The browser's Open-Meteo weather call uses your own IP, not the server's.
- No telemetry, no analytics, no third-party scripts in the served HTML.

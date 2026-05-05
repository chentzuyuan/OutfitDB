# Repo metadata (manual setup)

GitHub Actions' default `GITHUB_TOKEN` does **not** have permission to
edit a repo's About sidebar (description / homepage / topics) — those
endpoints need an `Administration: write` PAT, which we don't want to
maintain just for cosmetic settings. Set them in the UI instead:

> github.com/chentzuyuan/ClosetMind → top-right ⚙️ next to "About"

Paste these values:

**Description**

```
Local-first wardrobe app that learns your sense of temperature, aesthetic, and occasion — then suggests outfits from your own closet, including combos you'd never have tried yourself. Three-stage XGBoost pipeline + 11-table SQLite schema, no cloud, no account.
```

**Website**

```
https://chentzuyuan.github.io/closetmind.html
```

**Topics** (comma-separated; GitHub will validate them)

```
wardrobe, outfit-recommendation, machine-learning, xgboost, fastapi,
sqlite, sqlalchemy, local-first, personal-database, desktop-app,
pyinstaller, recommender-system
```

Tick **"Use your GitHub Pages website"** if it offers; that's the
chentzuyuan.github.io homepage URL above.

Click **Save changes**.

(The companion `repo-metadata.json` file in this folder keeps the same
values in machine-readable form, in case we ever wire up a PAT-based
automation later.)

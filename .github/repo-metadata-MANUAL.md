# Repo metadata (manual setup)

GitHub Actions' default `GITHUB_TOKEN` does **not** have permission to
edit a repo's About sidebar (description / homepage / topics) — those
endpoints need an `Administration: write` PAT, which we don't want to
maintain just for cosmetic settings. Set them in the UI instead:

> github.com/chentzuyuan/ClosetMind → top-right ⚙️ next to "About"

Paste these values:

**Description**

```
🌐 Try live: closetmind.onrender.com — Local-first wardrobe app that learns your sense of temperature, aesthetic, and occasion, then suggests outfits from your own closet (incl. combos you'd never have tried). Three-stage XGBoost + 11-table SQLite, no cloud, no account.
```

**Website**

```
https://closetmind.onrender.com/
```

Putting the live-demo URL here (instead of the landing page on
chentzuyuan.github.io) makes the GitHub repo page itself a
one-click trial — anyone who finds the project sees "Try live: …"
right in the description and can hit the demo from the sidebar
without leaving GitHub. The landing page is still linked from the
README badge row; nothing is lost.

**Topics** (comma-separated; GitHub will validate them)

```
wardrobe, outfit-recommendation, machine-learning, xgboost, fastapi,
sqlite, sqlalchemy, local-first, personal-database, desktop-app,
pyinstaller, recommender-system
```

Click **Save changes**.

(The companion `repo-metadata.json` file in this folder keeps the same
values in machine-readable form, in case we ever wire up a PAT-based
automation later.)

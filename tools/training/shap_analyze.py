#!/usr/bin/env python3
"""SHAP / feature_importance analysis on the Tester XGBoost model.

Builds the 100-feature vector for every rated outfit in the DB, loads the
XGBoost model, computes SHAP values via TreeExplainer, and prints:

  1. Top features by XGBoost gain  (what the trees split on most)
  2. Top features by mean(|SHAP|)  (what actually moves predictions)
  3. Per-block (context / color / material / coverage / etc.) summary
"""
import sys
from pathlib import Path
import numpy as np
import xgboost as xgb
import shap

CM_ROOT = Path("/Users/buttegg/School_Projects/wardrobe_env/closetapp")
sys.path.insert(0, str(CM_ROOT.parent))
sys.path.insert(0, str(CM_ROOT))

from app import models  # noqa
from app.database import get_db, _resolve_db_url  # noqa
from app.services.feature_engineering import (  # noqa
    build_feature_vector, COLOR_VOCAB, PATTERN_VOCAB,
    MATERIAL_VOCAB, WEATHER_VOCAB, OCCASION_VOCAB,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ─── Build feature names matching build_feature_vector() ───
def feature_names() -> list[str]:
    n = []
    # A: context
    n += ["ctx.temp_norm"]
    n += [f"ctx.weather.{w}" for w in WEATHER_VOCAB]
    n += [f"ctx.occasion.{o}" for o in OCCASION_VOCAB]
    # B: colors
    n += [f"color.{c}" for c in COLOR_VOCAB]
    # patterns
    n += [f"pattern.{p}" for p in PATTERN_VOCAB]
    n += ["pattern.complexity_avg"]
    # C: material per slot
    for slot in ("top", "bottom", "shoes"):
        n += [f"material.{slot}.{m}" for m in MATERIAL_VOCAB]
    # D: fit / length
    n += ["fit.shoulder_avg", "fit.waist_avg", "len.top_avg",
          "fit.pants_avg", "len.pants_avg", "fit.collar_avg"]
    # E: layering counts
    n += ["layer.inner", "layer.mid", "layer.outer", "layer.total_items"]
    # F: coverage
    n += ["cov.warmth", "cov.thick_max", "cov.thick_sum"]
    n += [f"cov.curve_k{k}" for k in range(4)]
    n += [f"cov.t_cover_k{k}" for k in range(4)]
    n += ["cov.optimal_k", "cov.overkill", "cov.underfit"]
    # F2: aesthetic
    n += [f"aes.curve_k{k}" for k in range(4)]
    n += ["aes.stops_count", "aes.underfit", "aes.final_k"]
    # G: history
    n += ["hist.avg_rating", "hist.coverage_count", "hist.days_since_worn"]
    return n


def main():
    db_url = _resolve_db_url()
    print(f"DB: {db_url}\n")
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    db = Session()

    # Build feature names
    fn = feature_names()
    print(f"Feature space: {len(fn)} dims\n")

    # Pull all rated outfits
    rated = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == 1)
        .all()
    )
    print(f"Rated outfits in DB: {len(rated)}")

    X = []
    y = []
    for r in rated:
        outfit = db.get(models.Outfit, r.outfit_id)
        if not outfit or outfit.context_id is None:
            continue
        ctx = db.get(models.DailyContext, outfit.context_id)
        if not ctx:
            continue
        try:
            vec = build_feature_vector(outfit, ctx)
            if vec.shape[0] != len(fn):
                continue
            X.append(vec)
            y.append(1 if r.rating >= 1 else 0)
        except Exception:
            continue

    X = np.array(X)
    y = np.array(y)
    print(f"Built feature matrix: {X.shape}, positive ratio {y.mean():.2f}\n")

    # Load model
    model_path = "/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/models/current.json"
    booster = xgb.Booster()
    booster.load_model(model_path)
    print(f"Model loaded: {model_path}\n")

    # ─── (1) Native gain importance ───
    importances = booster.get_score(importance_type="gain")
    # XGB names features as f0, f1, ... by index when no names given
    gain_pairs = []
    for k, v in importances.items():
        idx = int(k.lstrip("f"))
        if idx < len(fn):
            gain_pairs.append((fn[idx], v))
    gain_pairs.sort(key=lambda p: -p[1])
    print("─── Top 25 features by XGBoost gain ─────────────────────────────────")
    for name, g in gain_pairs[:25]:
        print(f"  {name:<32} {g:>10.3f}")

    # ─── (2) SHAP values ───
    print("\n─── Computing SHAP values ─────────────────────────────────────────")
    explainer = shap.TreeExplainer(booster)
    shap_vals = explainer.shap_values(X)
    mean_abs = np.abs(shap_vals).mean(axis=0)
    shap_pairs = sorted(zip(fn, mean_abs), key=lambda p: -p[1])
    print("─── Top 25 features by mean(|SHAP|) ────────────────────────────────")
    for name, s in shap_pairs[:25]:
        print(f"  {name:<32} {s:>10.4f}")

    # ─── (3) Block-level summary ───
    blocks = {
        "context (temp/weather/occasion)": [n for n in fn if n.startswith("ctx.")],
        "colors":                          [n for n in fn if n.startswith("color.")],
        "pattern":                         [n for n in fn if n.startswith("pattern.")],
        "material":                        [n for n in fn if n.startswith("material.")],
        "fit / length":                    [n for n in fn if n.startswith("fit.") or n.startswith("len.")],
        "layering counts":                 [n for n in fn if n.startswith("layer.")],
        "coverage (Layer Coverage Model)": [n for n in fn if n.startswith("cov.")],
        "aesthetic curve":                 [n for n in fn if n.startswith("aes.")],
        "history":                         [n for n in fn if n.startswith("hist.")],
    }
    name_to_idx = {n: i for i, n in enumerate(fn)}
    print("\n─── Block-level mean(|SHAP|) totals ─────────────────────────────")
    block_totals = []
    for label, names in blocks.items():
        idxs = [name_to_idx[n] for n in names if n in name_to_idx]
        total = float(mean_abs[idxs].sum())
        block_totals.append((label, total, len(idxs)))
    block_totals.sort(key=lambda p: -p[1])
    overall = sum(b[1] for b in block_totals)
    for label, total, n_dim in block_totals:
        pct = 100 * total / overall if overall else 0
        bar = "█" * int(pct / 2)
        print(f"  {label:<35} {total:>7.4f}  ({pct:>4.1f}%) {bar}")


if __name__ == "__main__":
    main()

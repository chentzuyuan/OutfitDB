import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc
from .. import models
from ..config import get_data_dir
from .feature_engineering import build_feature_vector, feature_version


LEGACY_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"


# ─── Sliding-window training cap ────────────────────────────────────────
# We train on the MOST RECENT N ratings only. Older ratings stay in the DB
# (history is never deleted) but don't enter training. This keeps:
#   • training time bounded (~1-3 sec regardless of total ratings)
#   • model adaptive to taste drift (old preferences fade automatically)
#   • UI free to allow unlimited "extra training" rounds without bloat
#
# 1500 is PROVISIONAL — chosen as a conservative upper bound. The proper
# justification (AUC vs N curve under real temporal drift) requires the
# user to have logged ≥30 days of actual wear data so `rated_at` reflects
# meaningful taste evolution, not synthetic batch-training timestamps.
# The sweep tool exists at `tools/training/window_sweep.py`; re-run it
# once that data is available and revise this number empirically.
MAX_TRAIN_ROWS = 1500


def _resolve_model_paths() -> Tuple[Path, Path]:
    """Return (current_model_path, archive_dir)."""
    data_dir = get_data_dir()
    if data_dir is not None:
        model_dir = data_dir / "models"
        archive_dir = model_dir / "archive"
        model_dir.mkdir(parents=True, exist_ok=True)
        archive_dir.mkdir(parents=True, exist_ok=True)
        return model_dir / "current.json", archive_dir
    LEGACY_MODEL_DIR.mkdir(exist_ok=True)
    return LEGACY_MODEL_DIR / "user_1_model.json", LEGACY_MODEL_DIR


def get_active_model_path() -> Path:
    return _resolve_model_paths()[0]


def _binary_label(rating_value: int) -> Optional[int]:
    if rating_value is None:
        return None
    if rating_value >= 1:
        return 1
    return 0


def train_model(db: Session, user_id: int) -> dict:
    # Sliding window: pull most recent MAX_TRAIN_ROWS, then reverse to
    # chronological order so the temporal 80/20 train-val split still
    # validates on the newest fraction (which is the realistic deployment
    # condition).
    recent = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user_id)
        .order_by(desc(models.Rating.rated_at))
        .limit(MAX_TRAIN_ROWS)
        .all()
    )
    ratings = list(reversed(recent))
    total_in_db = (
        db.query(models.Rating)
        .filter(models.Rating.user_id == user_id)
        .count()
    )
    samples_X = []
    samples_y = []

    for r in ratings:
        outfit = db.get(models.Outfit, r.outfit_id)
        if outfit is None or outfit.context_id is None:
            continue
        context = db.get(models.DailyContext, outfit.context_id)
        if context is None:
            continue
        label = _binary_label(r.rating)
        if label is None:
            continue
        try:
            vec = build_feature_vector(outfit, context)
        except Exception:
            continue
        samples_X.append(vec)
        samples_y.append(label)

    n = len(samples_X)
    if n < 10:
        return {
            "status": "skipped",
            "training_samples": n,
            "message": "需要至少 10 筆評分才能訓練",
        }

    try:
        import xgboost as xgb
        from sklearn.metrics import roc_auc_score, log_loss
    except Exception as e:
        return {"status": "error", "training_samples": n, "message": str(e)}

    X = np.array(samples_X, dtype=np.float32)
    y = np.array(samples_y, dtype=np.int32)
    split = max(1, int(n * 0.8))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    scale_pos_weight = (neg / pos) if pos > 0 else 1.0

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss",
        early_stopping_rounds=20,
        tree_method="hist",
    )

    val_auc = None
    val_logloss = None
    try:
        if len(X_val) >= 2 and len(set(y_val.tolist())) == 2:
            model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
            probs = model.predict_proba(X_val)[:, 1]
            val_auc = float(roc_auc_score(y_val, probs))
            val_logloss = float(log_loss(y_val, probs, labels=[0, 1]))
        else:
            # small dataset — no eval_set
            model.set_params(early_stopping_rounds=None)
            model.fit(X_train, y_train, verbose=False)
    except Exception as e:
        return {"status": "error", "training_samples": n, "message": str(e)}

    model_path, archive_dir = _resolve_model_paths()
    # archive previous model before overwriting
    if model_path.exists():
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(model_path), str(archive_dir / f"{ts}.json"))
    model.save_model(str(model_path))

    # deactivate old ModelRuns
    db.query(models.ModelRun).filter(
        models.ModelRun.user_id == user_id,
        models.ModelRun.is_active == True,
    ).update({models.ModelRun.is_active: False})
    run = models.ModelRun(
        user_id=user_id,
        model_path=str(model_path),
        training_samples=n,
        feature_version=feature_version(),
        val_auc=val_auc,
        val_logloss=val_logloss,
        is_active=True,
    )
    db.add(run)
    db.commit()

    trimmed = max(0, total_in_db - len(ratings))
    msg = f"已訓練並儲存模型（{n} 筆樣本"
    if trimmed > 0:
        msg += f",忽略 {trimmed} 筆較舊評分"
    msg += "）"

    return {
        "status": "ok",
        "training_samples": n,
        "total_ratings_in_db": total_in_db,
        "trimmed_old_ratings": trimmed,
        "val_auc": val_auc,
        "val_logloss": val_logloss,
        "model_path": str(model_path),
        "message": msg,
    }

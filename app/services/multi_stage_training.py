"""Phase 4 — train Stage 1 (temperature) and Stage 3 (occasion) classifiers.

Both stages are framed as binary classification with a one-hot zone/event
appended to the outfit feature vector. So one model handles all 6 zones
(stage 1) or all 9 events (stage 3); inference asks the model
"is this outfit OK for THIS zone/event?".

Stage 2 keeps using the existing model_training.train_model().
"""
from datetime import datetime
import shutil
from pathlib import Path
from typing import Optional, Tuple
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import models
from ..config import get_data_dir
from .feature_engineering import build_feature_vector
from .model_training import LEGACY_MODEL_DIR, MAX_TRAIN_ROWS


# ─── Model file paths ───────────────────────────────────────────────────
def _stage_paths(stage: str) -> Tuple[Path, Path]:
    """(current_model_path, archive_dir) for the given stage."""
    fname = {"temp": "stage1_temp.json", "occasion": "stage3_occasion.json"}[stage]
    data_dir = get_data_dir()
    if data_dir is not None:
        model_dir = data_dir / "models"
        archive_dir = model_dir / "archive"
        model_dir.mkdir(parents=True, exist_ok=True)
        archive_dir.mkdir(parents=True, exist_ok=True)
        return model_dir / fname, archive_dir
    LEGACY_MODEL_DIR.mkdir(exist_ok=True)
    return LEGACY_MODEL_DIR / fname, LEGACY_MODEL_DIR


def get_temp_model_path() -> Path:
    return _stage_paths("temp")[0]


def get_occasion_model_path() -> Path:
    return _stage_paths("occasion")[0]


# ─── One-hot helpers ────────────────────────────────────────────────────
def _zone_one_hot(zone_key: str) -> np.ndarray:
    """6-dim one-hot encoding of a temperature zone."""
    vec = np.zeros(len(models.TEMP_ZONE_KEYS), dtype=np.float32)
    if zone_key in models.TEMP_ZONE_KEYS:
        vec[models.TEMP_ZONE_KEYS.index(zone_key)] = 1.0
    return vec


def _event_one_hot(event_key: str) -> np.ndarray:
    """9-dim one-hot encoding of an event."""
    vec = np.zeros(len(models.EVENT_KEYS), dtype=np.float32)
    if event_key in models.EVENT_KEYS:
        vec[models.EVENT_KEYS.index(event_key)] = 1.0
    return vec


def _build_outfit_feat(outfit: models.Outfit, db: Session) -> Optional[np.ndarray]:
    """Build the 100-d feature vector. Synthesizes a context if outfit lacks one."""
    if outfit is None:
        return None
    ctx = None
    if outfit.context_id:
        ctx = db.get(models.DailyContext, outfit.context_id)
    if ctx is None:
        # synthesize a neutral context — only outfit features matter for stage1/3
        class _Ctx:
            temperature = 22.0
            temperature_high = 25.0
            temperature_low = 19.0
            weather = models.WeatherEnum.cloudy
            occasion = models.OccasionEnum.casual
        ctx = _Ctx()
    try:
        return build_feature_vector(outfit, ctx)
    except Exception:
        return None


# ─── Stage 1: Temperature classifier ────────────────────────────────────
def train_temperature_model(db: Session, user_id: int) -> dict:
    """Train a binary classifier: P(outfit OK for given zone) given
    outfit features + one-hot zone.

    Uses sliding window of MAX_TRAIN_ROWS most recent temperature ratings
    so training cost stays bounded as the user keeps rating."""
    total_in_db = (
        db.query(models.TemperatureRating)
        .filter(models.TemperatureRating.user_id == user_id)
        .count()
    )
    recent = (
        db.query(models.TemperatureRating)
        .filter(models.TemperatureRating.user_id == user_id)
        .order_by(desc(models.TemperatureRating.rated_at))
        .limit(MAX_TRAIN_ROWS)
        .all()
    )
    rows = list(reversed(recent))
    if not rows:
        return {"status": "skipped", "samples": 0, "message": "no temperature ratings yet"}

    X, y = [], []
    for r in rows:
        outfit = db.get(models.Outfit, r.outfit_id)
        feats = _build_outfit_feat(outfit, db)
        if feats is None:
            continue
        zones_ok = set(r.zones_ok or [])
        # Expand into 6 samples (one per zone) — multi-label as 6 binary tasks
        for zk in models.TEMP_ZONE_KEYS:
            full = np.concatenate([feats, _zone_one_hot(zk)])
            label = 1 if zk in zones_ok else 0
            X.append(full)
            y.append(label)

    n = len(X)
    if n < 30:
        return {"status": "skipped", "samples": n, "message": "need ≥30 expanded samples"}

    try:
        import xgboost as xgb
        from sklearn.metrics import roc_auc_score, log_loss
    except Exception as e:
        return {"status": "error", "samples": n, "message": str(e)}

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.int32)
    split = max(1, int(n * 0.8))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    scale_pos_weight = (neg / pos) if pos > 0 else 1.0

    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss", early_stopping_rounds=20,
        tree_method="hist",
    )

    val_auc, val_logloss = None, None
    try:
        if len(X_val) >= 2 and len(set(y_val.tolist())) == 2:
            model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
            probs = model.predict_proba(X_val)[:, 1]
            val_auc = float(roc_auc_score(y_val, probs))
            val_logloss = float(log_loss(y_val, probs, labels=[0, 1]))
        else:
            model.set_params(early_stopping_rounds=None)
            model.fit(X_train, y_train, verbose=False)
    except Exception as e:
        return {"status": "error", "samples": n, "message": str(e)}

    model_path, archive_dir = _stage_paths("temp")
    if model_path.exists():
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(model_path), str(archive_dir / f"stage1_{ts}.json"))
    model.save_model(str(model_path))

    trimmed = max(0, total_in_db - len(rows))
    msg = f"stage 1 (temperature) trained on {n} expanded samples ({len(rows)} raw ratings"
    if trimmed > 0:
        msg += f", {trimmed} older ratings skipped"
    msg += ")"

    return {
        "status": "ok",
        "samples": n,
        "raw_ratings_used": len(rows),
        "total_ratings_in_db": total_in_db,
        "trimmed_old_ratings": trimmed,
        "val_auc": val_auc,
        "val_logloss": val_logloss,
        "model_path": str(model_path),
        "message": msg,
    }


# ─── Stage 3: Occasion classifier ───────────────────────────────────────
def train_occasion_model(db: Session, user_id: int) -> dict:
    """Train Stage 3 occasion classifier with sliding-window cap."""
    total_in_db = (
        db.query(models.OccasionRating)
        .filter(models.OccasionRating.user_id == user_id)
        .count()
    )
    recent = (
        db.query(models.OccasionRating)
        .filter(models.OccasionRating.user_id == user_id)
        .order_by(desc(models.OccasionRating.rated_at))
        .limit(MAX_TRAIN_ROWS)
        .all()
    )
    rows = list(reversed(recent))
    if not rows:
        return {"status": "skipped", "samples": 0, "message": "no occasion ratings yet"}

    X, y = [], []
    for r in rows:
        outfit = db.get(models.Outfit, r.outfit_id)
        feats = _build_outfit_feat(outfit, db)
        if feats is None:
            continue
        events_ok = set(r.events_ok or [])
        for ek in models.EVENT_KEYS:
            full = np.concatenate([feats, _event_one_hot(ek)])
            label = 1 if ek in events_ok else 0
            X.append(full)
            y.append(label)

    n = len(X)
    if n < 30:
        return {"status": "skipped", "samples": n, "message": "need ≥30 expanded samples"}

    try:
        import xgboost as xgb
        from sklearn.metrics import roc_auc_score, log_loss
    except Exception as e:
        return {"status": "error", "samples": n, "message": str(e)}

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.int32)
    split = max(1, int(n * 0.8))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    scale_pos_weight = (neg / pos) if pos > 0 else 1.0

    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        eval_metric="logloss", early_stopping_rounds=20,
        tree_method="hist",
    )

    val_auc, val_logloss = None, None
    try:
        if len(X_val) >= 2 and len(set(y_val.tolist())) == 2:
            model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
            probs = model.predict_proba(X_val)[:, 1]
            val_auc = float(roc_auc_score(y_val, probs))
            val_logloss = float(log_loss(y_val, probs, labels=[0, 1]))
        else:
            model.set_params(early_stopping_rounds=None)
            model.fit(X_train, y_train, verbose=False)
    except Exception as e:
        return {"status": "error", "samples": n, "message": str(e)}

    model_path, archive_dir = _stage_paths("occasion")
    if model_path.exists():
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        shutil.move(str(model_path), str(archive_dir / f"stage3_{ts}.json"))
    model.save_model(str(model_path))

    trimmed = max(0, total_in_db - len(rows))
    msg = f"stage 3 (occasion) trained on {n} expanded samples ({len(rows)} raw ratings"
    if trimmed > 0:
        msg += f", {trimmed} older ratings skipped"
    msg += ")"

    return {
        "status": "ok",
        "samples": n,
        "raw_ratings_used": len(rows),
        "total_ratings_in_db": total_in_db,
        "trimmed_old_ratings": trimmed,
        "val_auc": val_auc,
        "val_logloss": val_logloss,
        "model_path": str(model_path),
        "message": msg,
    }


# ─── Inference helpers (used by scoring.py) ─────────────────────────────
_temp_model_cache = {"path_mtime": None, "model": None}
_occ_model_cache = {"path_mtime": None, "model": None}


def _load_xgb(path: Path, cache: dict):
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except Exception:
        return None
    if cache["model"] is not None and cache["path_mtime"] == mtime:
        return cache["model"]
    try:
        import xgboost as xgb
        m = xgb.XGBClassifier()
        m.load_model(str(path))
        cache["model"] = m
        cache["path_mtime"] = mtime
        return m
    except Exception:
        return None


def predict_temp_pass(outfit_features: np.ndarray, zone_key: str) -> float:
    """P(outfit appropriate for `zone_key`). Returns 1.0 (no filter) if model
    not trained yet, so the chain doesn't drop everything during cold start."""
    m = _load_xgb(get_temp_model_path(), _temp_model_cache)
    if m is None:
        return 1.0
    full = np.concatenate([outfit_features, _zone_one_hot(zone_key)]).reshape(1, -1)
    try:
        p = float(m.predict_proba(full)[0, 1])
        return max(0.0, min(1.0, p))
    except Exception:
        return 1.0


def predict_occasion_pass(outfit_features: np.ndarray, event_key: str) -> float:
    """P(outfit appropriate for `event_key`). 1.0 cold-start fallback."""
    m = _load_xgb(get_occasion_model_path(), _occ_model_cache)
    if m is None:
        return 1.0
    full = np.concatenate([outfit_features, _event_one_hot(event_key)]).reshape(1, -1)
    try:
        p = float(m.predict_proba(full)[0, 1])
        return max(0.0, min(1.0, p))
    except Exception:
        return 1.0


# ─── Mapping legacy OccasionEnum → 9-event keys ─────────────────────────
# Used when scoring against /recommend's existing context.occasion 5-value enum.
LEGACY_OCCASION_TO_EVENT = {
    "casual": "casual_outing",
    "work":   "office",
    "formal": "formal_event",
    "sport":  "gym",
    "home":   "home",
}


def occasion_enum_to_event(occ: Optional[models.OccasionEnum]) -> Optional[str]:
    if occ is None:
        return None
    return LEGACY_OCCASION_TO_EVENT.get(occ.value)


def temp_to_zone_key(temp: Optional[float]) -> Optional[str]:
    if temp is None:
        return None
    if temp < 0:  return "subzero"
    if temp < 10: return "cold"
    if temp < 18: return "cool"
    if temp < 25: return "mild"
    if temp < 30: return "warm"
    return "hot"

"""Empirical justification of the sliding-window training cap.

⚠️ PRECONDITION: this experiment is only meaningful when the user has
≥30 days (ideally ≥90 days) of REAL wear-and-rate data, so that the
`rated_at` timestamps actually reflect taste evolution. If most ratings
were generated in synthetic training-page batches over a short period,
"older" and "newer" are essentially the same distribution and the curve
will be flat / dominated by sampling noise. Re-run when there's real
temporal signal to detect.

Hypothesis (user-driven design choice): training on too much old data
hurts predictive AUC because user taste drifts over time. We expect the
val_auc curve to rise with more training data up to some sweet spot N*,
then plateau or decline as old ratings dilute the recent signal.

Methodology (apples-to-apples):
  1. Sort all aesthetic ratings chronologically.
  2. Hold out the most recent HOLDOUT_SIZE ratings as a fixed validation
     set V_test. This stays the same across all N — so AUC differences
     reflect only the training data, not the validation distribution.
  3. From the remaining ratings (= "available pool"), train XGBoost on
     the MOST RECENT n_train of them, where n_train ∈ N_VALUES.
  4. Evaluate every model on V_test → record AUC + log-loss.
  5. Plot AUC vs n_train.

Output:
  - tools/training/window_sweep_results.json
  - tools/training/window_sweep_results.png  (figure for the report)

Run from project root:
  cd closetmind && .venv/bin/python -m tools.training.window_sweep
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import List, Tuple

import numpy as np

from app.database import SessionLocal
from app import models
from app.services.feature_engineering import build_feature_vector
from sqlalchemy import asc


# ─── Experiment configuration ────────────────────────────────────────────
HOLDOUT_SIZE = 200          # fixed validation set = newest N ratings
N_VALUES = [50, 100, 200, 400, 600, 800, 1000, 1250,
            1500, 1750, 2000, 2250, 2500]
N_BOOTSTRAP = 50            # bootstrap iterations per N (variance estimate)
BASE_SEED = 42

OUT_DIR = Path(__file__).resolve().parent
JSON_PATH = OUT_DIR / "window_sweep_results.json"
PNG_PATH = OUT_DIR / "window_sweep_results.png"


# ─── Data prep ───────────────────────────────────────────────────────────
def _binary_label(rating_value: int) -> int | None:
    if rating_value is None:
        return None
    return 1 if rating_value >= 1 else 0


def load_dataset(user_id: int = 1) -> Tuple[np.ndarray, np.ndarray, list]:
    """Load all aesthetic ratings → features + labels in chronological order."""
    db = SessionLocal()
    try:
        rows = (
            db.query(models.Rating)
            .filter(models.Rating.user_id == user_id)
            .order_by(asc(models.Rating.rated_at))
            .all()
        )
        feats: List[np.ndarray] = []
        labels: List[int] = []
        timestamps: List = []
        for r in rows:
            outfit = db.get(models.Outfit, r.outfit_id)
            if outfit is None or outfit.context_id is None:
                continue
            ctx = db.get(models.DailyContext, outfit.context_id)
            if ctx is None:
                continue
            label = _binary_label(r.rating)
            if label is None:
                continue
            try:
                vec = build_feature_vector(outfit, ctx)
            except Exception:
                continue
            feats.append(vec)
            labels.append(label)
            timestamps.append(r.rated_at)
        return (np.array(feats, dtype=np.float32),
                np.array(labels, dtype=np.int32),
                timestamps)
    finally:
        db.close()


# ─── Single-N training ───────────────────────────────────────────────────
def train_and_eval(X_train: np.ndarray, y_train: np.ndarray,
                   X_val: np.ndarray, y_val: np.ndarray,
                   seed: int = BASE_SEED) -> dict:
    import xgboost as xgb
    from sklearn.metrics import roc_auc_score, log_loss

    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    spw = (neg / pos) if pos > 0 else 1.0

    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        scale_pos_weight=spw,
        eval_metric="logloss", early_stopping_rounds=20,
        tree_method="hist",
        # Subsample + colsample introduce real stochasticity so different
        # seeds produce different trees → meaningful bootstrap variance.
        subsample=0.85,
        colsample_bytree=0.85,
        random_state=seed,
    )
    # Need both classes in TRAIN — bootstrap can land on all-positive set
    if len(set(y_train.tolist())) < 2:
        return {"val_auc": None, "val_logloss": None}
    if len(set(y_val.tolist())) == 2:
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        probs = model.predict_proba(X_val)[:, 1]
        return {
            "val_auc": float(roc_auc_score(y_val, probs)),
            "val_logloss": float(log_loss(y_val, probs, labels=[0, 1])),
        }
    # degenerate case (val all-positive or all-negative): no AUC
    model.set_params(early_stopping_rounds=None)
    model.fit(X_train, y_train, verbose=False)
    return {"val_auc": None, "val_logloss": None}


def bootstrap_at_n(pool_X: np.ndarray, pool_y: np.ndarray, n: int,
                   X_val: np.ndarray, y_val: np.ndarray,
                   B: int = N_BOOTSTRAP) -> dict:
    """Bootstrap variance at a given training-window size N.

    For each of B iterations: take the most recent N ratings, then sample
    N rows WITH REPLACEMENT (Efron bootstrap), train, eval on fixed val.
    Reports mean / std / quantiles of val_auc and val_logloss."""
    rng = np.random.default_rng(BASE_SEED)
    base_X = pool_X[-n:]   # most recent N from the pool
    base_y = pool_y[-n:]
    aucs, lls = [], []
    for b in range(B):
        idx = rng.integers(0, n, size=n)
        Xb = base_X[idx]
        yb = base_y[idx]
        out = train_and_eval(Xb, yb, X_val, y_val, seed=BASE_SEED + b)
        if out["val_auc"] is not None:
            aucs.append(out["val_auc"])
            lls.append(out["val_logloss"])
    if not aucs:
        return {"n_train": n, "n_valid_runs": 0,
                "auc_mean": None, "auc_std": None, "auc_p25": None, "auc_p75": None,
                "ll_mean": None, "ll_std": None}
    aucs = np.array(aucs)
    lls = np.array(lls)
    return {
        "n_train": n,
        "n_valid_runs": len(aucs),
        "auc_mean": float(aucs.mean()),
        "auc_std": float(aucs.std(ddof=1)) if len(aucs) > 1 else 0.0,
        "auc_p25": float(np.percentile(aucs, 25)),
        "auc_p75": float(np.percentile(aucs, 75)),
        "ll_mean": float(lls.mean()),
        "ll_std": float(lls.std(ddof=1)) if len(lls) > 1 else 0.0,
        "ll_p25": float(np.percentile(lls, 25)),
        "ll_p75": float(np.percentile(lls, 75)),
    }


# ─── Sweep ───────────────────────────────────────────────────────────────
def run_sweep() -> dict:
    print(f"Loading aesthetic ratings...")
    X, y, ts = load_dataset()
    n_total = len(X)
    print(f"  total usable ratings: {n_total}")
    if n_total < HOLDOUT_SIZE + 100:
        raise SystemExit(f"Need ≥ {HOLDOUT_SIZE + 100} ratings, only have {n_total}")

    # Hold out the newest HOLDOUT_SIZE as fixed val
    X_val = X[-HOLDOUT_SIZE:]
    y_val = y[-HOLDOUT_SIZE:]
    pool_X = X[:-HOLDOUT_SIZE]
    pool_y = y[:-HOLDOUT_SIZE]
    n_pool = len(pool_X)
    print(f"  held-out val: {HOLDOUT_SIZE}  (newest)")
    print(f"  available pool: {n_pool}")
    print(f"  val balance: {(y_val==1).sum()} pos / {(y_val==0).sum()} neg")
    print()

    results = []
    for n in N_VALUES:
        if n > n_pool:
            print(f"  skip n={n} (pool only has {n_pool})")
            continue
        # Take the MOST RECENT n from the pool
        Xn = pool_X[-n:]
        yn = pool_y[-n:]
        t0 = time.perf_counter()
        out = train_and_eval(Xn, yn, X_val, y_val)
        train_sec = time.perf_counter() - t0
        out["n_train"] = n
        out["train_sec"] = train_sec
        out["pos_frac"] = float((yn == 1).mean())
        results.append(out)
        auc = out["val_auc"]
        auc_str = f"{auc:.4f}" if auc is not None else "  n/a"
        print(f"  n_train={n:<5}  val_auc={auc_str}  "
              f"logloss={out['val_logloss']:.4f}  "
              f"pos%={out['pos_frac']*100:5.1f}  "
              f"({train_sec:5.2f}s)")

    summary = {
        "user_id": 1,
        "stage": "aesthetic",
        "total_ratings_loaded": n_total,
        "holdout_size": HOLDOUT_SIZE,
        "pool_size": n_pool,
        "random_seed": RANDOM_SEED,
        "results": results,
    }
    JSON_PATH.write_text(json.dumps(summary, indent=2))
    print(f"\n→ JSON saved: {JSON_PATH}")
    return summary


# ─── Plot ────────────────────────────────────────────────────────────────
def make_plot(summary: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rs = [r for r in summary["results"] if r["val_auc"] is not None]
    ns = [r["n_train"] for r in rs]
    aucs = [r["val_auc"] for r in rs]
    ll = [r["val_logloss"] for r in rs]

    # Find argmax for annotation
    best_idx = max(range(len(rs)), key=lambda i: aucs[i])
    best_n = ns[best_idx]
    best_auc = aucs[best_idx]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    ax1.plot(ns, aucs, marker="o", linewidth=2, color="#1f77b4")
    ax1.axvline(best_n, color="#d62728", linestyle="--", alpha=0.6,
                label=f"argmax: N*={best_n} (AUC={best_auc:.4f})")
    ax1.axvline(1500, color="#2ca02c", linestyle=":", alpha=0.6,
                label="chosen cap: 1500")
    ax1.set_xlabel("Training-window size N (most recent ratings)")
    ax1.set_ylabel("Validation AUC (held-out newest 200)")
    ax1.set_title("Sliding-window cap sweep — aesthetic stage")
    ax1.grid(alpha=0.3)
    ax1.legend(loc="lower right", fontsize=9)

    ax2.plot(ns, ll, marker="s", linewidth=2, color="#ff7f0e")
    ax2.axvline(best_n, color="#d62728", linestyle="--", alpha=0.6)
    ax2.axvline(1500, color="#2ca02c", linestyle=":", alpha=0.6)
    ax2.set_xlabel("Training-window size N")
    ax2.set_ylabel("Validation log-loss")
    ax2.set_title("Log-loss curve (lower = better)")
    ax2.grid(alpha=0.3)

    fig.suptitle(
        f"ClosetMind Phase 4 — why we cap training at the most recent N "
        f"(user_id=1, {summary['total_ratings_loaded']} ratings, "
        f"fixed val={summary['holdout_size']})",
        fontsize=11, y=1.02,
    )
    fig.tight_layout()
    fig.savefig(PNG_PATH, dpi=150, bbox_inches="tight")
    print(f"→ PNG saved: {PNG_PATH}")
    print(f"→ argmax N* = {best_n} (val_auc = {best_auc:.4f})")


if __name__ == "__main__":
    summary = run_sweep()
    make_plot(summary)

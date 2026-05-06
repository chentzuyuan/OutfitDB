from datetime import datetime
from typing import List, Optional, Tuple
import os
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import desc
from .. import models
from .feature_engineering import build_feature_vector


# Anchor points for the warmth target curve. Used by both context_fit_score
# (soft penalty) and the candidate generator's hard temperature gate.
# Values are smooth to avoid cliffs that wrongly reject normal outfits at
# zone boundaries. Format: list of (temp °C, ideal warmth).
IDEAL_WARMTH_TABLE = [
    (-5.0, 14.0),
    (0.0,  12.5),
    (5.0,  11.0),
    (10.0,  9.5),
    (15.0,  8.0),
    (18.0,  7.0),
    (22.0,  6.0),
    (25.0,  5.0),
    (28.0,  4.0),
    (32.0,  3.0),
    (999.0, 2.5),
]


def _thermal_underlayer_assumption(temp: Optional[float]) -> float:
    """Implicit cold-weather thermal underlayer.

    When dressing for cold (especially below ~12°C), users almost always
    add thermal underwear / leggings as an invisible base layer. The model
    only sees the visible outfit, so we lower the warmth target to account
    for these implicit layers — otherwise it over-demands visible warmth
    and rejects perfectly normal cold-weather outfits (e.g. sweater +
    jeans + sneakers at 0°C, where the user is also wearing thermal pants
    underneath).

    The user's intuition: "下身就長褲短褲就好,因為一般來說冬天裡面都會
    再塞秋褲或是內搭褲" — long-vs-short is enough granularity for bottoms
    because winter assumes thermal underwear underneath.

    Ramp: 0 above 12°C, ~1.5 at 0°C, capped at 2.0 below -3°C.
    """
    if temp is None or temp >= 12.0:
        return 0.0
    return max(0.0, min(2.0, (12.0 - temp) * 0.15))


def ideal_warmth_for_temp(temp: Optional[float]) -> float:
    """Linearly interpolate between the anchor points so there are no
    step cliffs (e.g. previously 24°C → 5.0 but 25°C → 3.0, breaking
    candidates near the boundary).

    Below ~12°C we subtract the thermal-underlayer assumption (see
    `_thermal_underlayer_assumption`): in cold weather the visible
    outfit only needs to provide part of the total warmth, the rest
    comes from invisible thermals."""
    if temp is None:
        return 7.0
    pts = IDEAL_WARMTH_TABLE
    if temp <= pts[0][0]:
        base = pts[0][1]
    elif temp >= pts[-1][0]:
        base = pts[-1][1]
    else:
        base = 7.0
        for i in range(len(pts) - 1):
            t0, w0 = pts[i]
            t1, w1 = pts[i + 1]
            if t0 <= temp < t1:
                # linear interp
                f = (temp - t0) / (t1 - t0)
                base = w0 + f * (w1 - w0)
                break
    return max(2.0, base - _thermal_underlayer_assumption(temp))


def context_fit_score(
    outfit: models.Outfit,
    context: models.DailyContext,
    user_temp_offset: float = 0.0,
) -> float:
    """user_temp_offset: positive = user is more cold-sensitive (perceives colder),
    so we shift the effective temperature downward → encourages warmer outfits.

    Heat-overdress penalty: if warmth_score > 1.5 × ideal_warmth_for_temp(t),
    we deduct proportionally (caps at -0.6). This stops the model from
    recommending peacoats at 28°C just because the candidate has them rated
    high in cold contexts.
    """
    warmth = float(outfit.warmth_score or 0.0)
    effective = (context.temperature or 0.0) - user_temp_offset
    ideal = ideal_warmth_for_temp(effective)
    overdress_pen = 0.0
    if ideal > 0 and warmth > 1.5 * ideal:
        # Linear penalty: at 1.5× → 0, at 2.5× → -0.6
        overage_ratio = (warmth - 1.5 * ideal) / max(1.0, ideal)
        overdress_pen = -min(0.6, 0.6 * overage_ratio)

    # Layer Coverage path: overkill / underfit_flag are computed at generate time
    # using the global T_base. We apply the user's bias here as a soft correction:
    if context.temperature_high is not None or context.temperature_low is not None:
        score = 1.0 - 0.3 * (outfit.overkill_layers or 0)
        if outfit.underfit_flag:
            score -= 1.0
        # Slight reward for outfits whose warmth aligns with shifted comfort temp
        if user_temp_offset and context.temperature is not None:
            shifted = context.temperature - user_temp_offset
            ideal_shifted = ideal_warmth_for_temp(shifted)
            diff = abs(warmth - ideal_shifted)
            adj = -0.05 * diff
            score += adj
        score += overdress_pen
        return max(0.0, min(1.0, score))
    # fallback via single-temp ideal warmth (with user offset)
    diff = abs(warmth - ideal)
    return max(0.0, min(1.0, 1.0 - 0.2 * diff + overdress_pen))


def _load_active_model(user_id: int):
    try:
        import xgboost as xgb
    except Exception:
        return None
    from .model_training import get_active_model_path
    path = get_active_model_path()
    if not path.exists():
        return None
    model = xgb.XGBClassifier()
    try:
        model.load_model(str(path))
        return model
    except Exception:
        return None


def preference_score(
    outfit: models.Outfit,
    context: models.DailyContext,
    model=None,
) -> float:
    if model is not None:
        try:
            vec = build_feature_vector(outfit, context).reshape(1, -1)
            proba = float(model.predict_proba(vec)[0, 1])
            return max(0.0, min(1.0, proba))
        except Exception:
            pass
    # cold-start: mean of item average ratings
    vals = []
    for oi in outfit.outfit_items:
        if oi.item and oi.item.stats and oi.item.stats.average_rating is not None:
            vals.append(oi.item.stats.average_rating)
    if not vals:
        return 0.5
    # ratings in [-1,2] → normalize to [0,1]
    mean = sum(vals) / len(vals)
    return max(0.0, min(1.0, (mean + 1.0) / 3.0))


def freshness_score(outfit: models.Outfit) -> float:
    now = datetime.utcnow()
    diffs = []
    for oi in outfit.outfit_items:
        it = oi.item
        if it and it.stats and it.stats.last_worn_date:
            diffs.append(max(0.0, (now - it.stats.last_worn_date).total_seconds() / 86400.0))
        else:
            diffs.append(14.0)
    if not diffs:
        return 1.0
    return max(0.0, min(1.0, (sum(diffs) / len(diffs)) / 14.0))


def diversity_score(db: Session, outfit: models.Outfit, user_id: int) -> float:
    recent = (
        db.query(models.OutfitLog)
        .filter(models.OutfitLog.user_id == user_id, models.OutfitLog.final_worn_outfit_id.isnot(None))
        .order_by(desc(models.OutfitLog.logged_at))
        .limit(5)
        .all()
    )
    if not recent:
        return 1.0
    current_ids = {oi.item_id for oi in outfit.outfit_items}
    if not current_ids:
        return 1.0
    overlaps = []
    for log in recent:
        past = db.get(models.Outfit, log.final_worn_outfit_id)
        if not past:
            continue
        past_ids = {oi.item_id for oi in past.outfit_items}
        if not past_ids:
            continue
        overlap = len(current_ids & past_ids) / len(current_ids | past_ids)
        overlaps.append(overlap)
    if not overlaps:
        return 1.0
    return max(0.0, 1.0 - (sum(overlaps) / len(overlaps)))


def recovery_score(outfit: models.Outfit) -> float:
    covs = []
    for oi in outfit.outfit_items:
        if oi.item and oi.item.stats:
            covs.append(float(oi.item.stats.coverage_count or 0))
    if not covs:
        return 1.0
    avg_cov = sum(covs) / len(covs)
    return max(0.0, 1.0 - avg_cov / 20.0)


def score_outfit(
    db: Session,
    outfit: models.Outfit,
    context: models.DailyContext,
    user_id: int,
    model=None,
) -> dict:
    user = db.get(models.User, user_id)
    user_offset = float(user.temp_offset or 0.0) if user else 0.0
    pref = preference_score(outfit, context, model=model)
    ctx = context_fit_score(outfit, context, user_temp_offset=user_offset)
    fresh = freshness_score(outfit)
    div = diversity_score(db, outfit, user_id)
    rec = recovery_score(outfit)

    # ─── Phase 4: 3-stage chained scoring ────────────────────────────────
    # If stage 1 (temp) and/or stage 3 (occasion) models are trained, use
    # them as multiplicative gates. If not yet trained, predict_*_pass()
    # returns 1.0 (no filtering — falls back to old context_fit).
    from .multi_stage_training import (
        predict_temp_pass, predict_occasion_pass,
        temp_to_zone_key, occasion_enum_to_event,
    )
    stage1_pass = 1.0
    stage3_pass = 1.0
    try:
        feats = build_feature_vector(outfit, context)
        zone_key = temp_to_zone_key(context.temperature)
        if zone_key:
            stage1_pass = predict_temp_pass(feats, zone_key)
        event_key = occasion_enum_to_event(context.occasion)
        if event_key:
            stage3_pass = predict_occasion_pass(feats, event_key)
    except Exception:
        pass

    # Aesthetic / freshness / diversity / recovery blend (stage 2 + soft signals)
    aesthetic_blend = 0.65 * pref + 0.20 * fresh + 0.10 * div + 0.05 * rec
    # Final = ctx_fit (legacy temp soft) × stage1 (learned temp) × stage3 (learned occasion) × aesthetic
    total = ctx * stage1_pass * stage3_pass * aesthetic_blend

    codes = []
    if ctx >= 0.9:
        codes.append({"key": "expl.good_climate"})
    elif outfit.underfit_flag:
        codes.append({"key": "expl.underfit"})
    elif (outfit.overkill_layers or 0) >= 1:
        codes.append({"key": "expl.overkill", "vars": {"n": outfit.overkill_layers}})
    if fresh >= 0.8:
        codes.append({"key": "expl.fresh"})
    if div >= 0.8:
        codes.append({"key": "expl.diverse"})
    if rec >= 0.8:
        codes.append({"key": "expl.recovery"})
    if outfit.aesthetic_stop_layers:
        codes.append({"key": "expl.stops", "vars": {"layers": str(outfit.aesthetic_stop_layers)}})
    if not codes:
        codes.append({"key": "expl.default"})

    return {
        "total_score": float(total),
        "preference_score": float(pref),
        "context_fit_score": float(ctx),
        "freshness_score": float(fresh),
        "diversity_score": float(div),
        "recovery_score": float(rec),
        "stage1_temp_pass": float(stage1_pass),
        "stage3_occasion_pass": float(stage3_pass),
        "explanation_codes": codes,
    }


def layering_hints(
    outfit: models.Outfit,
    context: models.DailyContext,
    has_thermal_insoles: bool = False,
) -> List[str]:
    """Return i18n hint keys nudging the user to add invisible thermal
    layers (秋褲 / 內搭 / 發熱衣) when the visible outfit is at the cold
    end of its zone or pairs cold weather with low-insulation fabrics.

    When `has_thermal_insoles` is True the user has self-reported they
    already wear thermal innerwear as standard kit, so we soften the
    advice: only the very-cold "subzero" hint fires (and even then it
    skips the leggings line, since that's already implicit). For users
    who haven't toggled it on we surface the full set of nudges.

    The scoring model *already* assumes the user has thermal underwear
    in cold weather (see `_thermal_underlayer_assumption`), but a new
    user reading the recommendation card has no way to know that —
    these hints make that assumption visible.
    """
    hints: List[str] = []
    t = context.temperature if context else None
    if t is None:
        return hints

    items = [oi.item for oi in (outfit.outfit_items or []) if oi.item is not None]

    has_denim_bottom = any(
        getattr(it, "category", None) == models.CategoryEnum.bottom
        and (getattr(it, "material", "") or "").lower() == "denim"
        for it in items
    )

    THIN_BASE_MATERIALS = {"cotton", "linen", "rayon", "viscose", "modal"}
    has_thin_base = any(
        getattr(it, "category", None) == models.CategoryEnum.top
        and getattr(it, "thickness", None) == models.ThicknessEnum.thin
        and (getattr(it, "material", "") or "").lower() in THIN_BASE_MATERIALS
        and getattr(it, "layer_role", None) != models.LayerRoleEnum.outer
        for it in items
    )

    # Order matters: stronger advice trumps softer advice.
    if t <= 0:
        hints.append("hint.thermal_strong" if not has_thermal_insoles else "hint.subzero_with_insoles")
    elif not has_thermal_insoles and t <= 10 and has_denim_bottom:
        hints.append("hint.thermal_under_denim")
    elif not has_thermal_insoles and t <= 10 and has_thin_base:
        hints.append("hint.thermal_thin_base")
    return hints


def get_top_k_recommendations(
    db: Session,
    outfits: List[models.Outfit],
    context: models.DailyContext,
    user_id: int,
    top_k: int = 5,
) -> List[Tuple[models.Outfit, dict]]:
    model = _load_active_model(user_id)
    scored = []
    for o in outfits:
        scored.append((o, score_outfit(db, o, context, user_id, model=model)))
    scored.sort(key=lambda x: x[1]["total_score"], reverse=True)
    return scored[:top_k]

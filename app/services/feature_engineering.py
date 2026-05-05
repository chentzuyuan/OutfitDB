from typing import List, Optional
from datetime import datetime
import numpy as np
from .. import models


COLOR_VOCAB = [
    "black", "white", "gray", "navy", "blue", "brown", "beige",
    "green", "red", "pink", "yellow", "orange", "purple",
]
PATTERN_VOCAB = ["solid", "striped", "plaid", "floral", "graphic", "checked", "dotted", "other"]
MATERIAL_VOCAB = [
    "cotton", "wool", "linen", "polyester", "denim", "leather",
    "silk", "cashmere", "nylon", "fleece", "other",
]
WEATHER_VOCAB = ["sunny", "cloudy", "rainy", "snowy", "windy"]
OCCASION_VOCAB = ["casual", "work", "formal", "sport", "home"]

FIT_MAP = {"slim": 0.0, "regular": 0.5, "loose": 1.0}
LENGTH_MAP = {"short": 0.0, "regular": 0.5, "long": 1.0, "cropped": 0.0, "full": 1.0}
COLLAR_MAP = {"crew": 0.0, "v": 0.3, "polo": 0.5, "turtleneck": 0.7, "hooded": 0.9}


def _one_hot(val: Optional[str], vocab: List[str]) -> List[float]:
    return [1.0 if (val is not None and val == v) else 0.0 for v in vocab]


def _mean_or_zero(vals: List[float]) -> float:
    return float(np.mean(vals)) if vals else 0.0


def _safe_map(val: Optional[str], mapping: dict) -> Optional[float]:
    if val is None:
        return None
    return mapping.get(val.lower(), None)


def _material_flags_for_slot(material: Optional[str]) -> List[float]:
    """Legacy single-material → one-hot. Used as fallback when no item is
    available for a slot. Prefer `_composition_flags_for_item` when an Item
    is at hand (it handles blends)."""
    if not material:
        return [0.0] * len(MATERIAL_VOCAB)
    m = material.lower()
    return [1.0 if m == v else 0.0 for v in MATERIAL_VOCAB]


def _composition_flags_for_item(item: Optional["models.Item"]) -> List[float]:
    """Weighted multi-hot for an item's fabric blend.

    A 100% cotton item produces [..., 1.0, ...] (one hot dimension).
    A 40/60 cotton-linen blend produces [..., 0.4, ..., 0.6, ...] across
    two dimensions. Materials outside MATERIAL_VOCAB are bucketed into
    the 'other' slot. Returns zeros for None.

    Falls back to single-material one-hot if the item has no composition
    set (i.e. all legacy items keep behaving identically before any
    blends are entered)."""
    if item is None:
        return [0.0] * len(MATERIAL_VOCAB)
    comp = getattr(item, "composition", None)
    if not comp:
        return _material_flags_for_slot(item.material)
    flags = [0.0] * len(MATERIAL_VOCAB)
    other_idx = MATERIAL_VOCAB.index("other") if "other" in MATERIAL_VOCAB else -1
    for entry in comp:
        mat = (entry.get("material") if isinstance(entry, dict) else None) or ""
        pct = (entry.get("pct") if isinstance(entry, dict) else 0) or 0
        weight = max(0.0, min(1.0, float(pct) / 100.0))
        m = mat.lower()
        if m in MATERIAL_VOCAB:
            flags[MATERIAL_VOCAB.index(m)] += weight
        elif other_idx >= 0:
            flags[other_idx] += weight
    return flags


def build_feature_vector(
    outfit: models.Outfit,
    context: models.DailyContext,
    history: Optional[List[np.ndarray]] = None,
) -> np.ndarray:
    items = [oi.item for oi in outfit.outfit_items]
    tops = [i for i in items if i.category == models.CategoryEnum.top]
    bottoms = [i for i in items if i.category == models.CategoryEnum.bottom]
    shoes = [i for i in items if i.category == models.CategoryEnum.shoes]
    fullbodies = [i for i in items if i.category == models.CategoryEnum.fullbody]

    # A: context
    temp = context.temperature if context.temperature is not None else 20.0
    feats: List[float] = [temp / 40.0]
    feats += _one_hot(context.weather.value if context.weather else None, WEATHER_VOCAB)
    feats += _one_hot(context.occasion.value if context.occasion else None, OCCASION_VOCAB)

    # B: colors — flags (any item has this color)
    all_colors = set()
    for it in items:
        for c in (it.colors or []):
            all_colors.add(c.lower())
    feats += [1.0 if c in all_colors else 0.0 for c in COLOR_VOCAB]

    # patterns one-hot (most common)
    pattern_counts = {p: 0 for p in PATTERN_VOCAB}
    for it in items:
        p = (it.pattern or "solid").lower()
        if p in pattern_counts:
            pattern_counts[p] += 1
        else:
            pattern_counts["other"] += 1
    dominant = max(pattern_counts, key=pattern_counts.get)
    feats += _one_hot(dominant, PATTERN_VOCAB)

    feats.append(_mean_or_zero([float(i.pattern_complexity or 0) for i in items]))

    # C: material per-slot — uses composition (weighted multi-hot) when set,
    # otherwise single-material one-hot. Fullbody fills both top and bottom
    # slots when no top/bottom present (one-piece dress/jumpsuit).
    top_item = tops[0] if tops else (fullbodies[0] if fullbodies else None)
    bottom_item = bottoms[0] if bottoms else (fullbodies[0] if fullbodies else None)
    shoes_item = shoes[0] if shoes else None
    feats += _composition_flags_for_item(top_item)
    feats += _composition_flags_for_item(bottom_item)
    feats += _composition_flags_for_item(shoes_item)

    # D: fit / length
    shoulder_vals = [_safe_map(t.shoulder_fit, FIT_MAP) for t in tops]
    waist_vals = [_safe_map(t.waist_fit, FIT_MAP) for t in tops]
    top_length_vals = [_safe_map(t.top_length, LENGTH_MAP) for t in tops]
    collar_vals = [_safe_map(t.collar, COLLAR_MAP) for t in tops]
    pants_fit_vals = [_safe_map(b.pants_fit, FIT_MAP) for b in bottoms]
    pants_length_vals = [_safe_map(b.pants_length, LENGTH_MAP) for b in bottoms]

    feats.append(_mean_or_zero([v for v in shoulder_vals if v is not None]))
    feats.append(_mean_or_zero([v for v in waist_vals if v is not None]))
    feats.append(_mean_or_zero([v for v in top_length_vals if v is not None]))
    feats.append(_mean_or_zero([v for v in pants_fit_vals if v is not None]))
    feats.append(_mean_or_zero([v for v in pants_length_vals if v is not None]))
    feats.append(_mean_or_zero([v for v in collar_vals if v is not None]))

    # E: layering counts
    feats += [
        float(outfit.inner_count),
        float(outfit.mid_count),
        float(outfit.outer_count),
        float(outfit.total_items),
    ]

    # F: coverage
    feats.append(float(outfit.warmth_score))
    thicks = [models.THICKNESS_VALUES.get(i.thickness.value, 2) for i in items]
    feats.append(float(max(thicks) if thicks else 0))
    feats.append(float(sum(thicks) if thicks else 0))
    cov = list(outfit.coverage_curve or [0.0, 0.0, 0.0, 0.0])
    while len(cov) < 4:
        cov.append(cov[-1] if cov else 0.0)
    feats += [float(x) for x in cov[:4]]
    T_BASE, ALPHA = 30.0, 2.5
    feats += [float(T_BASE - ALPHA * c) for c in cov[:4]]
    feats.append(float(outfit.optimal_layer_count or 0))
    feats.append(float(outfit.overkill_layers or 0))
    feats.append(1.0 if outfit.underfit_flag else 0.0)

    # F2: aesthetic
    aes = list(outfit.aesthetic_curve or [0.0, 0.0, 0.0, 0.0])
    while len(aes) < 4:
        aes.append(0.0)
    feats += [float(x) for x in aes[:4]]
    stops = outfit.aesthetic_stop_layers or []
    feats.append(float(len(stops)))
    aesthetic_underfit = 1.0 if (not stops and outfit.optimal_layer_count) else 0.0
    feats.append(aesthetic_underfit)
    final_k = min(stops) if stops and outfit.optimal_layer_count else 0
    feats.append(float(final_k))

    # G: history
    avg_ratings = [i.stats.average_rating for i in items if i.stats and i.stats.average_rating is not None]
    feats.append(_mean_or_zero(avg_ratings))
    cov_counts = [float(i.stats.coverage_count if i.stats else 0) for i in items]
    feats.append(_mean_or_zero(cov_counts))
    now = datetime.utcnow()
    day_diffs = []
    for i in items:
        if i.stats and i.stats.last_worn_date:
            day_diffs.append(max(0.0, (now - i.stats.last_worn_date).total_seconds() / 86400.0))
    feats.append(_mean_or_zero(day_diffs) if day_diffs else 30.0)

    return np.array(feats, dtype=np.float32)


def feature_version() -> str:
    # v2: material slot changed from one-hot to weighted multi-hot to support
    # fabric blends (e.g. 40% cotton 60% linen). Existing 100%-material items
    # produce identical features to v1, but blended items now get distinct
    # encodings, so cached models trained on v1 should retrain on first
    # opportunity to learn from the richer signal.
    return "v2"

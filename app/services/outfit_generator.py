import random
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from .. import models


T_BASE = 30.0
ALPHA = 2.5

# Hard temperature gate — outfits outside this band are rejected at
# candidate generation, before scoring. Temperature is absolute priority;
# aesthetic preference is only computed on temp-appropriate candidates.
WARMTH_LOWER_RATIO = 0.55  # below 55% of ideal → too cold (underfit)
WARMTH_UPPER_RATIO = 1.7   # above 170% of ideal → too hot (heat overdress)


def _ideal_warmth_for_temp(temp: Optional[float]) -> float:
    """Delegates to scoring.ideal_warmth_for_temp for a single source of
    truth (smooth piecewise-linear curve, no boundary cliffs)."""
    from .scoring import ideal_warmth_for_temp as _real
    return _real(temp)


def _passes_temperature_gate(warmth: float, context: "models.DailyContext") -> bool:
    """Hard temperature gate: outfit must produce warmth within
    [0.6×, 1.5×] of the ideal warmth for the context temperature."""
    temp = context.temperature
    if temp is None:
        return True  # no gate if no temp specified
    ideal = _ideal_warmth_for_temp(temp)
    if ideal <= 0:
        return True
    if warmth < WARMTH_LOWER_RATIO * ideal:
        return False  # underdressed for cold
    if warmth > WARMTH_UPPER_RATIO * ideal:
        return False  # overdressed for warm
    return True


# ─── STAGE 2: Occasion-formality gate ─────────────────────────────────────
# Each item has a formality score 0-5 (0 = beachwear / athletic, 5 = black-tie).
# Each occasion has a minimum required formality. Wearing UP is fine
# (suit at casual is allowed), wearing DOWN is rejected.
#
# We score by NAME-KEYWORD because item.style_tags isn't always reliable.
# Category-level fallbacks below.

# Specific item-name keyword overrides (highest priority)
_FORMALITY_KEYWORD_RULES = [
    # 5 — black-tie formal
    ("tuxedo jacket", 5),
    # 4 — formal / dressy
    ("dress shirt", 4),
    ("dress pants", 4),
    ("tuxedo trousers", 4),
    ("wool trousers", 4),
    ("peacoat", 4),
    ("overcoat", 4),
    ("fedora", 4),
    ("oxford shirt", 3),  # named lower because it's a casual button-up
    ("oxford", 4),  # oxford SHOES (after oxford shirt)
    ("scarf", 4),
    ("pocket square", 4),
    ("tie", 4),
    # 3 — smart-casual / business-casual
    ("trench", 3),
    ("wool coat", 3),
    ("down puffer", 3),
    ("leather jacket", 3),
    ("chinos", 3),
    ("v-neck sweater", 3),
    ("cable knit", 3),
    ("cardigan", 3),
    ("loafer", 3),
    ("chelsea", 3),
    ("combat boots", 3),
    ("dress", 3),
    ("linen shirt", 3),
    # 2 — casual smart
    ("polo", 2),
    ("bomber", 2),
    ("sweater vest", 2),
    ("skinny jeans", 2),
    ("dark wash jeans", 2),
    ("espadrille", 2),
    ("belt", 2),
    # 1 — casual
    ("t-shirt", 1),
    ("striped tee", 1),
    ("striped t-shirt", 1),
    ("light wash jeans", 1),
    ("jeans", 1),
    ("cargo shorts", 1),
    ("high-top", 1),
    ("running shoes", 1),
    ("sneaker", 1),  # generic sneakers
    # 0 — athletic / beachwear
    ("tank top", 0),
    ("athletic shorts", 0),
    ("basketball shorts", 0),
    ("joggers", 0),
    ("slip-on", 0),
    ("hoodie", 0),
    ("sweatshirt", 0),
    ("windbreaker", 0),
    ("beanie", 0),
    ("baseball cap", 0),
]

# Required minimum formality per occasion (gate threshold)
_OCCASION_FORMALITY_REQUIRED = {
    "formal": 3.0,
    "work": 1.8,
    "casual": 0.0,
    "sport": 0.0,
    "home": 0.0,
}


def _item_formality(item: models.Item) -> float:
    """Return formality score 0-5 for an item by name keyword + style_tag fallback."""
    name = (item.name or "").lower()
    for keyword, score in _FORMALITY_KEYWORD_RULES:
        if keyword in name:
            return float(score)
    # Fallback by style_tags
    tags = item.style_tags or []
    if "formal" in tags or "business" in tags:
        return 3.5
    if "sport" in tags or "athleisure" in tags:
        return 0.5
    if "casual" in tags:
        return 1.5
    return 2.0  # neutral default


def _outfit_formality(items: List[models.Item]) -> float:
    """Outfit-level formality: mean of {top, bottom, shoes, fullbody} scores
    (excluding accessories — accessories don't gate, they decorate)."""
    core_cats = {
        models.CategoryEnum.top,
        models.CategoryEnum.bottom,
        models.CategoryEnum.shoes,
        models.CategoryEnum.fullbody,
    }
    cores = [i for i in items if i.category in core_cats]
    if not cores:
        return 2.0
    return sum(_item_formality(i) for i in cores) / len(cores)


def _passes_occasion_gate(items: List[models.Item], context: "models.DailyContext") -> bool:
    """Hard occasion-formality gate (two-pronged check):

    1. AVERAGE core formality must meet the occasion's minimum
       (overall vibe is dressy enough).
    2. NO core item may be more than `weak_link_tolerance` below required
       (no single visible piece torpedoes the look — e.g., running shoes
       at a formal event, hoodie at the office).

    Wearing UP is always OK (a tuxedo at a casual lunch passes); wearing
    DOWN is rejected.
    """
    if not context.occasion:
        return True
    required = _OCCASION_FORMALITY_REQUIRED.get(context.occasion.value, 0.0)
    if required <= 0:
        return True

    core_cats = {
        models.CategoryEnum.top,
        models.CategoryEnum.bottom,
        models.CategoryEnum.shoes,
        models.CategoryEnum.fullbody,
    }
    cores = [i for i in items if i.category in core_cats]
    if not cores:
        return True
    scores = [_item_formality(i) for i in cores]

    # 1. Avg check
    avg = sum(scores) / len(scores)
    if avg < required:
        return False

    # 2. Weak-link check: no item more than 1.5 levels below required.
    #    formal (req=3) → reject anything <1.5 (tees=1, jeans=1, sneakers=1, joggers=0)
    #    work   (req=2) → reject anything <0.5 (hoodie=0, athletic shorts=0, joggers=0)
    #    Smart-casual hybrids (jeans+bomber+chinos at work) still pass since
    #    bomber/jeans both ≥ 1.
    weak_link_tolerance = 1.5
    if any(s < required - weak_link_tolerance for s in scores):
        return False

    return True


def _thickness_val(item: models.Item) -> int:
    return models.THICKNESS_VALUES.get(item.thickness.value, 2)


def _assign_layers(tops: List[models.Item]) -> List[models.Item]:
    # sort by layer_role priority then thickness asc
    role_order = {
        models.LayerRoleEnum.inner: 0,
        models.LayerRoleEnum.mid: 1,
        models.LayerRoleEnum.outer: 2,
        models.LayerRoleEnum.none: 1,
    }
    return sorted(tops, key=lambda t: (role_order.get(t.layer_role, 1), _thickness_val(t)))


def _compute_curves(
    layered_uppers: List[models.Item],
    bottoms: List[models.Item],
    fullbodies: List[models.Item],
) -> Dict:
    """Layer Coverage with fullbody support.
    - Upper stack = layered_uppers (already sorted, includes any fullbodies)
    - Lower coverage = sum of fullbody thicknesses + sum of bottom thicknesses
      (fullbody counts in BOTH upper layering AND lower base — it's one piece covering both)
    """
    bottom_w = sum(_thickness_val(b) for b in bottoms) + sum(_thickness_val(f) for f in fullbodies)
    coverage_curve: List[float] = []
    aesthetic_curve: List[float] = []
    cum = 0
    for u in layered_uppers:
        cum += _thickness_val(u)
        coverage_curve.append(float(cum + bottom_w))
        aesthetic_curve.append(1.0 if u.can_wear_alone else 0.0)
    while len(coverage_curve) < 4:
        coverage_curve.append(coverage_curve[-1] if coverage_curve else float(bottom_w))
        aesthetic_curve.append(0.0)
    t_cover = [T_BASE - ALPHA * c for c in coverage_curve]
    return {
        "coverage_curve": coverage_curve,
        "aesthetic_curve": aesthetic_curve,
        "t_cover": t_cover,
    }


def _match_layers(
    coverage_curve: List[float],
    aesthetic_curve: List[float],
    t_high: Optional[float],
    t_low: Optional[float],
    t_single: Optional[float],
    num_real_tops: int,
) -> Dict:
    t_cover = [T_BASE - ALPHA * c for c in coverage_curve]
    # determine comfort-required k: smallest k where t_cover[k-1] <= t_low
    target_low = t_low if t_low is not None else (t_single if t_single is not None else 20.0)
    target_high = t_high if t_high is not None else (t_single if t_single is not None else target_low)

    optimal_k_comfort = None
    for k in range(1, num_real_tops + 1):
        if t_cover[k - 1] <= target_low:
            optimal_k_comfort = k
            break
    underfit = optimal_k_comfort is None

    aesthetic_stop_layers = [k for k in range(1, num_real_tops + 1) if aesthetic_curve[k - 1] >= 1.0]

    final_k = None
    aesthetic_underfit = False
    if not underfit:
        candidates = [k for k in aesthetic_stop_layers if k >= optimal_k_comfort]
        if candidates:
            final_k = min(candidates)
        else:
            aesthetic_underfit = True

    overkill_layers = 0
    if final_k is not None:
        overkill_layers = max(0, num_real_tops - final_k)

    return {
        "optimal_layer_count": optimal_k_comfort,
        "final_k": final_k,
        "overkill_layers": overkill_layers,
        "underfit_flag": underfit,
        "aesthetic_underfit": aesthetic_underfit,
        "aesthetic_stop_layers": aesthetic_stop_layers,
    }


def _layer_counts(uppers: List[models.Item]) -> Dict[str, int]:
    """Counts on the combined upper stack (tops + fullbodies)."""
    inner = sum(1 for t in uppers if t.layer_role == models.LayerRoleEnum.inner)
    mid = sum(1 for t in uppers if t.layer_role == models.LayerRoleEnum.mid)
    outer = sum(1 for t in uppers if t.layer_role == models.LayerRoleEnum.outer)
    return {"inner": inner, "mid": mid, "outer": outer}


def _warmth_score(items: List[models.Item]) -> float:
    layer_items = [i for i in items if i.category in (
        models.CategoryEnum.top, models.CategoryEnum.bottom, models.CategoryEnum.fullbody
    )]
    if not layer_items:
        return 0.0
    thicks = [_thickness_val(i) for i in layer_items]
    uppers = [i for i in items if i.category in (
        models.CategoryEnum.top, models.CategoryEnum.fullbody
    )]
    counts = _layer_counts(uppers)
    return 0.7 * sum(thicks) + 0.3 * max(thicks) + 0.5 * (counts["mid"] + counts["outer"])


def _candidate_is_legal(
    uppers: List[models.Item],
    fullbodies: List[models.Item],
    context: "Optional[models.DailyContext]" = None,
    bypass_outer_cap: bool = False,
    skip_outerwear_alone: bool = False,
) -> bool:
    """Legality check for an upper-stack composition.

    `context` (optional): when supplied, the outer cap is relaxed in cold
        weather (≤ COLD_OUTER_RELAX_TEMP_C, default 8°C) so users can
        legitimately layer e.g. a peacoat OVER a suit jacket. Without
        context the rule stays strict (1 outer max) — backwards compatible.
    `bypass_outer_cap`: relaxes the outer-count cap to 2 even outside cold
        weather. Used by the "Try anyway" override path.
    `skip_outerwear_alone`: skips the outerwear-alone rule (which normally
        rejects an outfit composed only of outers without an inner/mid
        underneath). Used by:
          (a) the pinned-set pre-check, where random fill in the loop will
              add an inner shirt before the per-iteration check runs;
          (b) the "Try anyway" path, since the user is explicitly opting
              into the unusual combo.
    """
    # at most 1 fullbody (can't wear 2 dresses)
    if len(fullbodies) > 1:
        return False
    counts = _layer_counts(uppers)
    if counts["inner"] > 1 or counts["mid"] > 2:
        return False
    # Outer cap: normally 1, relaxed to 2 under either condition:
    #   (a) cold weather (real-world layered formal — peacoat over blazer)
    #   (b) explicit user override via bypass_outer_cap
    outer_cap = 1
    if bypass_outer_cap:
        outer_cap = 2
    elif context is not None and context.temperature is not None and context.temperature <= COLD_OUTER_RELAX_TEMP_C:
        outer_cap = 2
    if counts["outer"] > outer_cap:
        return False
    # Outerwear-alone rule.
    # `Item.can_wear_alone` for an OUTER-role item means
    # "this piece is OK over bare skin" — defaults to False for menswear coats
    # / jackets / puffers (they need an inner shirt), True for womenswear
    # statement pieces (bralette, kimono, harness) that work without an
    # underlayer. This way the rule extends across genders without
    # hard-coding a profile.gender field.
    if (
        not skip_outerwear_alone
        and counts["outer"] >= 1
        and counts["inner"] == 0
        and counts["mid"] == 0
        and not fullbodies
    ):
        outer_items = [t for t in uppers if t.layer_role == models.LayerRoleEnum.outer]
        if not any(t.can_wear_alone for t in outer_items):
            return False
    return True


# Below this temperature we automatically allow 2 outer layers (e.g.
# peacoat over suit jacket) without needing the user to flip the override.
COLD_OUTER_RELAX_TEMP_C = 8.0


def build_outfit_from_items(
    items: List[models.Item],
    context: models.DailyContext,
) -> Optional[Dict]:
    """Compute outfit dict for a user-curated set of items (no random generation).
    Returns None if items can't form a valid outfit."""
    tops = [i for i in items if i.category == models.CategoryEnum.top]
    bottoms = [i for i in items if i.category == models.CategoryEnum.bottom]
    fullbodies = [i for i in items if i.category == models.CategoryEnum.fullbody]
    upper_stack = tops + fullbodies
    if not bottoms and not fullbodies:
        return None
    if not _candidate_is_legal(upper_stack, fullbodies, context=context):
        return None
    layered_uppers = _assign_layers(upper_stack)
    curves = _compute_curves(layered_uppers, bottoms, fullbodies)
    num_real = len(layered_uppers)
    match = _match_layers(
        curves["coverage_curve"],
        curves["aesthetic_curve"],
        context.temperature_high,
        context.temperature_low,
        context.temperature,
        num_real,
    )
    counts = _layer_counts(layered_uppers)
    return {
        "items": items,
        "warmth_score": _warmth_score(items),
        "coverage_curve": curves["coverage_curve"],
        "aesthetic_curve": curves["aesthetic_curve"],
        "t_cover": curves["t_cover"],
        "optimal_layer_count": match["optimal_layer_count"],
        "final_k": match["final_k"],
        "overkill_layers": match["overkill_layers"],
        "underfit_flag": match["underfit_flag"],
        "aesthetic_stop_layers": match["aesthetic_stop_layers"],
        "inner_count": counts["inner"],
        "mid_count": counts["mid"],
        "outer_count": counts["outer"],
        "total_items": len(items),
    }


def _pick_anchor(
    tops: List[models.Item],
    bottoms: List[models.Item],
    shoes: List[models.Item],
    fullbodies: List[models.Item],
) -> Optional[models.Item]:
    """Pick a candidate anchor item to seed an outfit. Items with low
    coverage_count (rarely-worn) are preferred so we surface neglected
    pieces, but we sample from a top-30 pool with weighted randomness so
    we don't deterministically anchor to the same item every call (which
    causes pathological rejection patterns when that item happens to fail
    a hard rule like outerwear-can't-be-worn-alone)."""
    all_items = tops + bottoms + shoes + fullbodies

    def cov(i: models.Item) -> int:
        return i.stats.coverage_count if i.stats else 0

    def avg(i: models.Item) -> float:
        r = i.stats.average_rating if i.stats else None
        return r if r is not None else 0.0

    # Sort by (coverage_count asc, avg_rating asc) — rare + low-rated first
    sorted_items = sorted(all_items, key=lambda i: (cov(i), avg(i)))
    # Limit to the bottom-30 (most-neglected) and sample with linear-decay weights
    pool = sorted_items[:30] if len(sorted_items) > 30 else sorted_items
    if pool:
        # Weight: index 0 → highest weight, decays to 1 at the tail
        weights = [len(pool) - i for i in range(len(pool))]
        # Try up to 6 random picks, ensuring feasibility constraints below
        for _ in range(6):
            anchor = random.choices(pool, weights=weights, k=1)[0]
            # Move this anchor to the front so the feasibility-check loop below
            # tries it first. (Falls back to remaining sorted_items if anchor
            # itself isn't feasible.)
            sorted_items = [anchor] + [s for s in sorted_items if s is not anchor]
            break
    has_upper = lambda: bool(tops or fullbodies)
    has_lower = lambda: bool(bottoms or fullbodies)
    for anchor in sorted_items:
        # need to be able to fill upper + lower + shoes around this anchor
        if anchor.category == models.CategoryEnum.top and (not has_lower() or not shoes):
            continue
        if anchor.category == models.CategoryEnum.bottom and (not has_upper() or not shoes):
            continue
        if anchor.category == models.CategoryEnum.shoes and (not has_upper() or not has_lower()):
            continue
        if anchor.category == models.CategoryEnum.fullbody and not shoes:
            continue
        return anchor
    return None


def generate_candidates(
    db: Session,
    context: models.DailyContext,
    user_id: int,
    n: int = 100,
    must_include_item_id: Optional[int] = None,
    must_include_item_ids: Optional[List[int]] = None,
    bypass_pinned_legality: bool = False,
) -> List[Dict]:
    """Generate candidate outfits.

    `bypass_pinned_legality`: when True, the legality check on the
        force-included pinned set is relaxed (e.g. allowing 2 outers).
        The rest of the rules (no 2 fullbodies, no 3 outers, etc.)
        still apply. Use case: user explicitly clicks "Try anyway"
        after seeing a soft-block warning."""
    items = (
        db.query(models.Item)
        .filter(models.Item.user_id == user_id, models.Item.is_active == True)
        .all()
    )
    # Wearable states: clean (never worn since last wash) and worn (used but not yet
    # at wears_per_wash threshold). in_laundry and unavailable are blocked.
    _WEARABLE_STATES = {models.ItemStateEnum.clean, models.ItemStateEnum.worn}
    items = [i for i in items if not i.state or i.state.state in _WEARABLE_STATES]

    tops = [i for i in items if i.category == models.CategoryEnum.top]
    bottoms = [i for i in items if i.category == models.CategoryEnum.bottom]
    shoes = [i for i in items if i.category == models.CategoryEnum.shoes]
    accessories = [i for i in items if i.category == models.CategoryEnum.accessory]
    fullbodies = [i for i in items if i.category == models.CategoryEnum.fullbody]

    # need (top OR fullbody) AND (bottom OR fullbody) AND shoes
    if not (tops or fullbodies) or not (bottoms or fullbodies) or not shoes:
        return []

    pinned_ids: set[int] = set()
    if must_include_item_id is not None:
        pinned_ids.add(must_include_item_id)
    if must_include_item_ids:
        pinned_ids.update(must_include_item_ids)

    pinned_items = [i for i in items if i.id in pinned_ids]
    pinned_tops = [i for i in pinned_items if i.category == models.CategoryEnum.top]
    pinned_bottoms = [i for i in pinned_items if i.category == models.CategoryEnum.bottom]
    pinned_shoes = [i for i in pinned_items if i.category == models.CategoryEnum.shoes]
    pinned_fullbodies = [i for i in pinned_items if i.category == models.CategoryEnum.fullbody]
    if len(pinned_bottoms) > 1 or len(pinned_shoes) > 1 or len(pinned_fullbodies) > 1:
        return []
    # Pinned-set pre-check: enforce structural impossibilities (too many
    # of any single layer slot, too many fullbodies, too many outers for
    # the temperature) but skip outerwear-alone — that gets re-checked
    # on the FINAL outfit in the loop after random fill adds an inner.
    if not _candidate_is_legal(
        pinned_tops + pinned_fullbodies,
        pinned_fullbodies,
        context=context,
        bypass_outer_cap=bypass_pinned_legality,
        skip_outerwear_alone=True,
    ):
        return []

    if pinned_items:
        anchor = pinned_items[0]
    else:
        anchor = _pick_anchor(tops, bottoms, shoes, fullbodies)
        if anchor is None:
            return []

    t_high = context.temperature_high
    t_low = context.temperature_low
    t_single = context.temperature

    candidates: List[Dict] = []
    seen_signatures = set()
    max_attempts = n * 12
    attempts = 0
    relaxed = False

    while len(candidates) < n and attempts < max_attempts:
        attempts += 1
        # start with all pinned items (or just the anchor if no pins)
        if pinned_items:
            chosen: List[models.Item] = list(pinned_items)
        else:
            chosen = [anchor]
        chosen_ids = {i.id for i in chosen}

        def _has(cat):
            return any(i.category == cat for i in chosen)
        has_fullbody = _has(models.CategoryEnum.fullbody)
        has_upper = has_fullbody or _has(models.CategoryEnum.top)
        has_lower = has_fullbody or _has(models.CategoryEnum.bottom)
        has_shoes = _has(models.CategoryEnum.shoes)

        def _add_fullbody():
            nonlocal has_fullbody, has_upper, has_lower
            avail = [f for f in fullbodies if f.id not in chosen_ids]
            if not avail:
                return False
            f = random.choice(avail)
            chosen.append(f); chosen_ids.add(f.id)
            has_fullbody = True; has_upper = True; has_lower = True
            return True

        # If both upper+lower empty and a fullbody exists: use it 30% of the time
        if not has_upper and not has_lower and fullbodies and random.random() < 0.3:
            _add_fullbody()

        # Ensure upper: prefer top, fall back to fullbody
        if not has_upper:
            avail = [t for t in tops if t.id not in chosen_ids]
            if avail:
                t = random.choice(avail)
                chosen.append(t); chosen_ids.add(t.id); has_upper = True
            else:
                _add_fullbody()

        # Ensure lower: prefer bottom, fall back to fullbody
        if not has_lower:
            avail = [b for b in bottoms if b.id not in chosen_ids]
            if avail:
                b = random.choice(avail)
                chosen.append(b); chosen_ids.add(b.id); has_lower = True
            else:
                _add_fullbody()

        # Optionally add extra tops for layering (more conservative if fullbody is base)
        extra_top_prob = 0.35 if has_fullbody else 0.55
        extra_tops_pool = [t for t in tops if t.id not in chosen_ids]
        while extra_tops_pool and random.random() < extra_top_prob:
            t = random.choice(extra_tops_pool)
            chosen.append(t)
            chosen_ids.add(t.id)
            extra_tops_pool = [x for x in extra_tops_pool if x.id not in chosen_ids]
            extra_top_prob *= 0.5

        if not has_shoes:
            avail = [s for s in shoes if s.id not in chosen_ids]
            if avail:
                s = random.choice(avail)
                chosen.append(s); chosen_ids.add(s.id); has_shoes = True

        if accessories and random.random() < 0.3:
            avail = [a for a in accessories if a.id not in chosen_ids]
            if avail:
                a = random.choice(avail)
                chosen.append(a); chosen_ids.add(a.id)

        # dedupe
        sig = tuple(sorted(chosen_ids))
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        chosen_tops = [i for i in chosen if i.category == models.CategoryEnum.top]
        chosen_bottoms = [i for i in chosen if i.category == models.CategoryEnum.bottom]
        chosen_fullbodies = [i for i in chosen if i.category == models.CategoryEnum.fullbody]
        # combined upper stack: tops + fullbodies (sorted by layer_role + thickness)
        upper_stack = chosen_tops + chosen_fullbodies
        # Per-iteration final-outfit check. Cold-weather + bypass paths
        # propagate via context + bypass_outer_cap. When bypass is on,
        # also skip outerwear-alone — the user explicitly opted in.
        if not _candidate_is_legal(
            upper_stack, chosen_fullbodies,
            context=context,
            bypass_outer_cap=bypass_pinned_legality,
            skip_outerwear_alone=bypass_pinned_legality,
        ):
            continue
        # need lower coverage: either bottom or fullbody
        if not chosen_bottoms and not chosen_fullbodies:
            continue

        layered_uppers = _assign_layers(upper_stack)
        curves = _compute_curves(layered_uppers, chosen_bottoms, chosen_fullbodies)
        num_real = len(layered_uppers)
        match = _match_layers(
            curves["coverage_curve"],
            curves["aesthetic_curve"],
            t_high,
            t_low,
            t_single,
            num_real,
        )

        # Layer-coverage / aesthetic checks. When bypass is on, skip them
        # entirely — the user explicitly opted into an "unusual" outfit.
        if bypass_pinned_legality:
            pass
        elif not relaxed:
            if match["underfit_flag"]:
                continue
            if match["aesthetic_underfit"]:
                continue
            if match["overkill_layers"] >= 2:
                continue
        else:
            if match["overkill_layers"] >= 3:
                continue

        counts = _layer_counts(layered_uppers)
        warmth = _warmth_score(chosen)

        # ─── STAGE 1: HARD TEMPERATURE GATE ──────────────────────────────
        # Temperature is absolute priority. Reject outfits outside the
        # acceptable warmth band before they enter the candidate pool.
        # When the user explicitly clicked "Try anyway" we skip this entirely
        # — they're saying "I know this looks wrong for the weather, show me
        # the outfit anyway".
        if bypass_pinned_legality:
            pass  # full override: no temperature filtering at all
        elif not relaxed:
            if not _passes_temperature_gate(warmth, context):
                continue
        else:
            # In relaxed mode (when wardrobe can't fill the pool), allow
            # ±10% extra slack rather than disabling the temp gate entirely.
            temp = context.temperature
            if temp is not None:
                ideal = _ideal_warmth_for_temp(temp)
                if ideal > 0:
                    if warmth < 0.5 * ideal or warmth > 1.7 * ideal:
                        continue

        # ─── STAGE 2: HARD OCCASION-FORMALITY GATE ───────────────────────
        # Outfit's core-item formality must meet the occasion's minimum.
        # Dressing UP is allowed (suit at casual = OK); dressing DOWN is not.
        # Bypass also honors this — if the user pinned a tank top for a
        # formal event and clicked "Try anyway", show it.
        if not bypass_pinned_legality and not _passes_occasion_gate(chosen, context):
            continue

        candidates.append(
            {
                "items": chosen,
                "warmth_score": warmth,
                "coverage_curve": curves["coverage_curve"],
                "aesthetic_curve": curves["aesthetic_curve"],
                "t_cover": curves["t_cover"],
                "optimal_layer_count": match["optimal_layer_count"],
                "final_k": match["final_k"],
                "overkill_layers": match["overkill_layers"],
                "underfit_flag": match["underfit_flag"],
                "aesthetic_stop_layers": match["aesthetic_stop_layers"],
                "inner_count": counts["inner"],
                "mid_count": counts["mid"],
                "outer_count": counts["outer"],
                "total_items": len(chosen),
            }
        )

        if (not relaxed) and attempts >= max_attempts // 2 and len(candidates) < n // 4:
            relaxed = True

    return candidates


def persist_candidates(
    db: Session,
    candidates: List[Dict],
    context_id: int,
    user_id: int,
) -> List[models.Outfit]:
    saved = []
    for c in candidates:
        outfit = models.Outfit(
            user_id=user_id,
            context_id=context_id,
            warmth_score=c["warmth_score"],
            inner_count=c["inner_count"],
            mid_count=c["mid_count"],
            outer_count=c["outer_count"],
            total_items=c["total_items"],
            is_generated=True,
            coverage_curve=c["coverage_curve"],
            aesthetic_curve=c["aesthetic_curve"],
            optimal_layer_count=c["optimal_layer_count"],
            overkill_layers=c["overkill_layers"],
            underfit_flag=c["underfit_flag"],
            aesthetic_stop_layers=c["aesthetic_stop_layers"],
        )
        db.add(outfit)
        db.flush()
        for pos, item in enumerate(c["items"]):
            db.add(models.OutfitItem(outfit_id=outfit.id, item_id=item.id, position=pos))
        saved.append(outfit)
    db.commit()
    for o in saved:
        db.refresh(o)
    return saved

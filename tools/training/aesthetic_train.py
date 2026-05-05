#!/usr/bin/env python3
"""Add aesthetically-grounded ratings to the active Tester profile and retrain.

Goes through the existing 500 unrated outfits in the Tester DB and scores each
one with a "normal-person logic + aesthetic" rubric:

  - Color cohesion (palette size, neutrals, jarring pairs)
  - Style coherence (formal vs casual mix)
  - Occasion fit (tuxedo at casual = bad)
  - Layer / temperature sanity
  - Practical/aesthetic red flags (e.g., tank top under tuxedo)

Each outfit gets a final integer rating in [-1, 2]:
   2  excellent fit + cohesive aesthetic
   1  reasonable, would actually wear
   0  meh, technically wearable
  -1  obviously wrong / clashing

Talks only to the running FastAPI at 127.0.0.1:8000 — no DB writes outside HTTP.
"""
import json
import sys
import requests
from collections import Counter

BASE = "http://127.0.0.1:8000"

# ─── Aesthetic rules ─────────────────────────────────────────────────────
NEUTRALS = {"white", "black", "gray", "navy", "beige"}
WARM_COLORS = {"red", "brown", "beige"}
COOL_COLORS = {"blue", "navy", "green", "gray"}

# Pairs that always look bad (rare in this 40-item wardrobe but defensible)
CLASH_PAIRS = {
    frozenset({"red", "green"}),       # Christmas-party only
    frozenset({"red", "blue"}),        # only OK if one is navy
}

FORMAL_TAGS = {"formal", "business"}
CASUAL_TAGS = {"casual", "sport", "athleisure", "outdoor"}


# ─── Modern outfit formulas (2024-2026 trends) ──────────────────────────
# Each formula returns +2 if matched (signature look), +1 if partial.
def _has_any(names, *keywords):
    return any(any(kw in n for kw in keywords) for n in names)

def style_formula_score(items: list[dict]) -> int:
    """Recognize known modern outfit formulas.
    Returns +2 for a perfectly executed formula, +1 partial, 0 not matched."""
    names = [(it.get("name") or "").lower() for it in items]
    cats = [it.get("category") for it in items]
    materials = [(it.get("material") or "").lower() for it in items]
    palette = set()
    for it in items:
        for c in (it.get("colors") or []):
            palette.add(c.lower())

    # Old Money / Quiet Luxury — polo/oxford/cashmere + chinos/trousers + loafers/chelsea
    has_om_top    = _has_any(names, "polo", "oxford shirt", "dress shirt", "cardigan", "v-neck sweater", "cable knit")
    has_om_bottom = _has_any(names, "chino", "trousers", "dress pants")
    has_om_shoes  = _has_any(names, "loafer", "chelsea", "oxford")
    if has_om_top and has_om_bottom and has_om_shoes and palette.issubset(NEUTRALS | {"brown"}):
        return 2  # full old-money look
    if (has_om_top and has_om_bottom) or (has_om_top and has_om_shoes):
        return 1

    # Smart Casual — blazer/tuxedo jacket + jeans/chinos + sneakers/loafers
    has_sc_top    = _has_any(names, "tuxedo jacket", "bomber", "leather jacket", "wool coat", "peacoat", "overcoat")
    has_sc_bottom = _has_any(names, "jeans", "chino")
    has_sc_shoes  = _has_any(names, "sneaker", "loafer", "chelsea", "high-top", "slip-on")
    if has_sc_top and has_sc_bottom and has_sc_shoes:
        return 2  # blazer+jeans+sneakers / tuxedo jacket+jeans

    # Normcore / Minimal — plain tee + jeans + white sneakers (palette ≤ 2 neutrals)
    has_n_top    = _has_any(names, "t-shirt", "tank top", "tee")
    has_n_bottom = _has_any(names, "jeans", "chino")
    has_n_shoes  = _has_any(names, "sneaker", "high-top")
    if has_n_top and has_n_bottom and has_n_shoes and palette.issubset(NEUTRALS):
        return 2

    # Athleisure — hoodie/sweatshirt + joggers/athletic shorts + sneakers
    has_a_top    = _has_any(names, "hoodie", "sweatshirt", "windbreaker")
    has_a_bottom = _has_any(names, "joggers", "athletic short", "basketball short")
    has_a_shoes  = _has_any(names, "running", "sneaker", "high-top", "slip-on")
    if (has_a_top and has_a_bottom) or (has_a_bottom and has_a_shoes and has_n_top):
        return 2

    # Coastal / Vacation — linen shirt + chinos/shorts + espadrilles or sneakers
    if _has_any(names, "linen") and _has_any(names, "chino", "cargo short") \
       and _has_any(names, "espadrille", "loafer", "sneaker"):
        return 2

    # Tonal / Monochrome — only 1 non-neutral color, full neutral palette
    non_neutral = palette - NEUTRALS
    if len(non_neutral) == 0 and len(palette) >= 2:
        return 1  # all-neutral tonal

    # Dark Academia — sweater vest + dress shirt + trousers
    if _has_any(names, "sweater vest") and _has_any(names, "dress shirt", "oxford shirt") \
       and _has_any(names, "trousers", "chino"):
        return 2

    # Layered cold-weather — 3+ items including outer + cardigan/sweater + button-up
    if (sum(c == "top" for c in cats) >= 3
        and _has_any(names, "sweater", "cardigan", "vest", "knit")
        and _has_any(names, "shirt", "polo", "tee", "t-shirt")):
        return 1

    return 0


def temperature_ceiling_score(items: list[dict], temp: float) -> int:
    """Penalize obvious overdressing: heavy coats above 22°C, very_thick above 18°C."""
    thicknesses = [(it.get("thickness") or "").lower() for it in items]
    names = [(it.get("name") or "").lower() for it in items]
    has_very_thick = "very_thick" in thicknesses
    has_heavy_outer = _has_any(names, "wool coat", "overcoat", "peacoat", "trench coat",
                                "down puffer", "tuxedo jacket")
    if temp >= 25 and (has_very_thick or has_heavy_outer):
        return -2  # parka in summer
    if temp >= 22 and has_very_thick:
        return -1  # very thick at warm temp
    if temp >= 18 and _has_any(names, "wool overcoat", "down puffer"):
        return -1
    return 0


def color_score(colors_per_item: list[list[str]]) -> int:
    """Score color palette: +1 cohesive, 0 ok, -1 clash."""
    flat = [c for cs in colors_per_item for c in cs]
    palette = set(flat)
    if not palette:
        return 0
    # All-neutral palette = elegant
    non_neutral = palette - NEUTRALS
    if len(non_neutral) == 0:
        return 1
    # 1 accent color in a neutral base = classic
    if len(non_neutral) == 1 and len(palette - non_neutral) >= 1:
        return 1
    # 2+ non-neutral colors => check for clash
    for pair in CLASH_PAIRS:
        if pair.issubset(palette):
            return -1
    if len(non_neutral) >= 3:
        return -1  # too busy
    return 0


def style_score(items: list[dict]) -> int:
    """+1 cohesive, 0 ok, -1 clashing styles.
    Smart casual (formal jacket + casual pants/shoes) is a recognized
    modern outfit formula and gets +1, NOT a penalty."""
    tags = []
    for it in items:
        tags.extend(it.get("style_tags") or [])
    counts = Counter(tags)
    has_formal = any(t in FORMAL_TAGS for t in counts)
    has_casual = any(t in CASUAL_TAGS for t in counts)
    has_sport  = "sport" in counts or "athleisure" in counts
    # Hard clash: formal + sport in same outfit (tuxedo + running shoes)
    if has_formal and has_sport:
        return -1
    # formal + casual = smart casual = +1 (handled by style_formula_score too)
    if len(counts) > 0:
        return 1
    return 0


def occasion_score(items: list[dict], occasion: str) -> int:
    """+2 perfect fit, +1 fits, 0 ok, -1 wrong, -2 obviously wrong.
    Returns wider range than other rubric components so occasion mismatch
    actually moves the final rating (was diluted to ~0 in v1)."""
    names = [(it.get("name") or "").lower() for it in items]
    if occasion == "formal":
        formal_top    = any("tuxedo jacket" in n or "dress shirt" in n
                            or "wool coat" in n or "trench" in n or "peacoat" in n
                            or "overcoat" in n for n in names)
        formal_bottom = any("dress pants" in n or "tuxedo trousers" in n
                            or "wool trousers" in n for n in names)
        formal_shoes  = any("oxford" in n and "shirt" not in n for n in names) \
                        or any("loafer" in n or "chelsea" in n for n in names)
        casual_leak   = any("tank" in n or "cargo short" in n or "joggers" in n
                            or "running" in n or "espadrille" in n or "athletic short" in n
                            or "basketball short" in n or "hoodie" in n
                            or "sweatshirt" in n or "high-top" in n
                            or "slip-on" in n or "windbreaker" in n
                            or "beanie" in n or "baseball cap" in n for n in names)
        if casual_leak:
            return -2
        score = 0
        if formal_top: score += 1
        if formal_bottom: score += 1
        if formal_shoes: score += 1
        return min(score - 1, 1)  # need 2/3 to get +1
    if occasion == "work":
        if any("tank" in n or "cargo short" in n or "joggers" in n
               or "tuxedo" in n or "athletic short" in n or "basketball short" in n
               or "running shoe" in n or "hoodie" in n or "windbreaker" in n
               or "baseball cap" in n for n in names):
            return -2
        work_pos = any("polo" in n or "dress shirt" in n or "oxford shirt" in n
                       or "chino" in n or "dress pants" in n or "trousers" in n
                       or "sweater" in n or "cardigan" in n for n in names)
        return 1 if work_pos else 0
    if occasion == "sport":
        if any("dress shirt" in n or "tuxedo" in n or "oxford" in n
               or "loafer" in n or "chelsea boots" in n or "fedora" in n for n in names):
            return -2
        if any("joggers" in n or "running" in n or "athletic" in n or "basketball" in n
               or "hoodie" in n or "sweatshirt" in n or "windbreaker" in n
               or "tee" in n or "tank" in n or "high-top" in n
               or "slip-on" in n for n in names):
            return 1
        return 0
    if occasion == "home":
        if any("tuxedo" in n or "dress shirt" in n or "trench" in n
               or "fedora" in n for n in names):
            return -2
        return 0
    # casual
    if any("tuxedo" in n for n in names):
        return -2
    return 1


def layer_score(coverage: float, temp: float) -> int:
    """+1 right warmth, 0 a bit off, -1 way off."""
    # Loose mapping: warmth_score ≈ how heavy the outfit is
    # Cold (<10) wants high warmth, hot (>27) wants low warmth
    if temp < 10 and coverage < 5:
        return -1
    if temp > 27 and coverage > 5:
        return -1
    if temp < 15 and coverage < 4:
        return -1
    if 15 <= temp <= 25 and 3 <= coverage <= 7:
        return 1
    return 0


def red_flags(items: list[dict]) -> int:
    """Catch specific anti-patterns that an actual person would notice."""
    names = [(it.get("name") or "").lower() for it in items]
    cats  = [it.get("category") for it in items]
    flat_text = " | ".join(names)
    pen = 0
    # Tuxedo + tank top: tuxedo expects a dress shirt underneath
    if "tuxedo" in flat_text and ("tank" in flat_text or "tee" in flat_text and "t-shirt" in flat_text):
        pen -= 1
    # Two hats / two belts
    if sum("belt" in n for n in names) >= 2:
        pen -= 1
    if sum(("beanie" in n) or ("fedora" in n) for n in names) >= 2:
        pen -= 1
    # Heavy outerwear + no bottoms
    if sum(c == "bottom" for c in cats) == 0 and not any("tuxedo" in n or "jumpsuit" in n for n in names):
        pen -= 1
    return pen


def rate_outfit(outfit: dict, ctx_temp: float, ctx_occasion: str) -> int:
    items = [it["item"] for it in outfit.get("outfit_items", []) if it.get("item")]
    if not items:
        return -1
    cs = color_score([it.get("colors") or [] for it in items])
    st = style_score(items)
    oc = occasion_score(items, ctx_occasion)
    ly = layer_score(outfit.get("warmth_score") or 0.0, ctx_temp)
    rf = red_flags(items)
    fm = style_formula_score(items)              # +0 / +1 / +2 modern formula
    tc = temperature_ceiling_score(items, ctx_temp)  # 0 / -1 / -2 overdress

    # raw = color + style + 2×occasion + layer + red_flags + formula + 2×temp_ceiling
    raw = cs + st + 2 * oc + ly + rf + fm + 2 * tc
    # Map raw [-11..+8] → final [-1..+2]
    if raw <= -2:
        return -1
    if raw <= 0:
        return 0
    if raw <= 2:
        return 1
    return 2


# ─── Driver ─────────────────────────────────────────────────────────────
SCENARIOS = [
    # date_iso,          temp, hi, lo, weather,  occasion
    ("2026-05-01T08:00:00", 32.0, 34, 28, "sunny",  "casual"),
    ("2026-05-01T09:00:00", 28.0, 30, 24, "sunny",  "sport"),
    ("2026-05-01T10:00:00", 24.0, 26, 20, "sunny",  "casual"),
    ("2026-05-01T11:00:00", 22.0, 25, 19, "cloudy", "work"),
    ("2026-05-01T12:00:00", 20.0, 23, 17, "cloudy", "casual"),
    ("2026-05-01T13:00:00", 16.0, 19, 13, "cloudy", "work"),
    ("2026-05-01T14:00:00", 14.0, 17, 11, "rainy",  "formal"),
    ("2026-05-01T15:00:00", 10.0, 12, 7,  "cloudy", "formal"),
    ("2026-05-01T16:00:00", 8.0,  10, 5,  "cloudy", "casual"),
    ("2026-05-01T17:00:00", 18.0, 21, 15, "sunny",  "home"),
]


def main():
    # Sanity: must talk to Tester
    p = requests.get(f"{BASE}/profiles", timeout=5).json()
    if p.get("active") != "Tester":
        print(f"ABORT: active profile is {p.get('active')!r}, expected 'Tester'")
        sys.exit(1)

    new_ratings = 0
    rating_dist = Counter()
    rejected_pre = 0

    for date_iso, t, hi, lo, weather, occasion in SCENARIOS:
        ctx = requests.post(f"{BASE}/contexts/", json={
            "date": date_iso, "temperature": t, "temperature_high": hi,
            "temperature_low": lo, "weather": weather, "occasion": occasion,
        }, timeout=10).json()
        if "id" not in ctx:
            print(f"  skip ctx {weather}/{occasion} {t}°C: {ctx}")
            continue

        recs = requests.post(f"{BASE}/recommendations/", json={
            "context_id": ctx["id"], "top_k": 15,
        }, timeout=60).json()

        if isinstance(recs, dict):
            print(f"  no recs for {weather}/{occasion} {t}°C: {recs}")
            rejected_pre += 1
            continue

        scen_ratings = []
        for r in recs:
            outfit = r["outfit"]
            score = rate_outfit(outfit, t, occasion)
            scen_ratings.append(score)
            rr = requests.post(f"{BASE}/ratings/", json={
                "outfit_id": outfit["id"],
                "rating": score,
                "rating_source": "user_rated",
            }, timeout=10)
            if rr.status_code == 200:
                new_ratings += 1
                rating_dist[score] += 1
        avg = sum(scen_ratings) / max(1, len(scen_ratings))
        dist = Counter(scen_ratings)
        print(f"  {weather:>6}/{occasion:<7} {t:>5.1f}°C: rated {len(scen_ratings):>2} "
              f"(avg={avg:+.2f}, +2:{dist[2]} +1:{dist[1]} 0:{dist[0]} -1:{dist[-1]})")

    print(f"\nTotal new ratings submitted: {new_ratings}")
    print(f"Distribution: {dict(rating_dist)}")
    if rejected_pre:
        print(f"Scenarios skipped (no candidates): {rejected_pre}")

    # Retrain
    print("\nTriggering /train/ ...")
    tr = requests.post(f"{BASE}/train/", timeout=120).json()
    print(f"Train result: {tr}")


if __name__ == "__main__":
    main()

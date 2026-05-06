"""Seed the Tester profile with baseline temperature + occasion ratings.

Goal: ship a fully-trained Tester profile inside seed_profiles/Tester so
first-launch users (DMG / ZIP / Render demo) see "good outfits" right
away, not "model still cold-starting" warmth-only fallbacks. Users then
diverge from this baseline as they rate their own outfits.

Heuristics (deliberately simple, deterministic):

  Temperature
    Pick zones around the outfit's warmth_score. The thermal model maps
    warmth_score → a "minimum comfortable temperature" via
    `T_cover(k) = 30 - 2.5 * Ck` so warmth ~3 ≈ mild, warmth ~6 ≈ cool,
    warmth ~9 ≈ cold, warmth ~12 ≈ subzero. We accept the outfit for
    target_zone whenever the score is plausible there, and add adjacent
    zones if the slack is tight.

  Occasion
    Look at the union of every item's `style_tags` plus category. Map:
      "formal" / "business" / "office"  → office, business_meeting, interview
      "tuxedo" / "evening"              → formal_event
      "sport" / "athletic" / "gym"      → gym
      "swim" / "beach"                  → beach
      "casual" / "minimal" / no tags    → casual, home, date_night

Run from project root with uvicorn already serving Tester on port 18888:

  .venv/bin/python -m tools.training.seed_tester_baseline

The script POSTs through the public API endpoints (no DB-writes from
Python) so a future schema change in /temp/submit or /occasion/submit
gets exercised the same way the front-end exercises it.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


BASE = "http://127.0.0.1:18888"
TARGET_PER_ZONE = 18  # min_per_zone is 15; over-shoot a touch for stability
TARGET_OCCASION = 65  # min_required is 60


# ─── HTTP helpers ─────────────────────────────────────────────────────────
def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def _post(path: str, body: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            try:
                return r.status, json.loads(r.read())
            except json.JSONDecodeError:
                return r.status, {}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


# ─── Stage 1: Temperature ────────────────────────────────────────────────
ZONE_KEYS = ["subzero", "cold", "cool", "mild", "warm", "hot"]


def _zones_for_warmth(w: float, target: str) -> list[str]:
    """Return the zone(s) the outfit is plausible for, given its warmth_score
    and the system's current target_zone (so we can bias toward agreeing
    with the system on the zone we're being probed for, when reasonable).

    Anchor: warmth ≈ 3 → mild (~22 °C); each +3 warmth ≈ -10 °C.
    Bands kept generous (Δ=2) so a single outfit usually accepts 2–3
    adjacent zones, which gives the classifier signal across boundaries
    rather than knife-edge yes/no labels.
    """
    out: set[str] = set()
    if w >= 11:
        out.update({"subzero", "cold"})
    if 8 <= w <= 13:
        out.update({"cold", "cool"})
    if 5 <= w <= 9:
        out.update({"cool", "mild"})
    if 2 <= w <= 6:
        out.update({"mild", "warm"})
    if 0 <= w <= 4:
        out.update({"warm", "hot"})
    if w <= 2:
        out.add("hot")
    # Always include the target zone if it's at all plausible — otherwise
    # the adaptive sampler keeps picking the same probe forever.
    if target in {"subzero", "hot"} or abs(_zone_to_anchor(target) - _zone_to_anchor_for_warmth(w)) <= 1:
        out.add(target)
    return sorted(out, key=ZONE_KEYS.index)


def _zone_to_anchor(zone: str) -> int:
    return ZONE_KEYS.index(zone)


def _zone_to_anchor_for_warmth(w: float) -> int:
    if w >= 11: return 0
    if w >= 8:  return 1
    if w >= 5:  return 2
    if w >= 2:  return 3
    if w >= 0:  return 4
    return 5


def train_temperature() -> None:
    print("\n── Stage 1: Temperature ──")
    while True:
        prog = _get("/training/v2/temp/progress")
        zones = {z["key"]: z["total"] for z in prog["zones"]}
        gaps = [k for k, v in zones.items() if v < TARGET_PER_ZONE]
        if not gaps:
            print(f"  ✓ all zones at >= {TARGET_PER_ZONE} ratings")
            break
        print(f"  gaps: {gaps}  (totals: " + " ".join(f"{k}={v}" for k,v in zones.items()) + ")")
        try:
            batch = _get("/training/v2/temp/next")
        except urllib.error.HTTPError as e:
            print(f"  /temp/next HTTP {e.code}: {e.read().decode()[:120]}")
            return
        if batch.get("done"):
            print("  /temp/next reports done, stopping")
            break
        target = batch["target_zone"]
        ratings = []
        for o in batch["outfits"]:
            ok = _zones_for_warmth(o["warmth_score"], target)
            ratings.append({"outfit_id": o["outfit_id"], "zones_ok": ok})
        st, resp = _post("/training/v2/temp/submit", {"ratings": ratings})
        if st != 200:
            print(f"  /temp/submit HTTP {st}: {resp}")
            return
        print(f"  +{len(ratings)} ratings (target={target})")


# ─── Stage 3: Occasion ───────────────────────────────────────────────────
EVENT_KEYS = [
    "home", "gym", "beach", "casual_outing", "date_night",
    "interview", "office", "business_meeting", "formal_event",
]


def _events_for_outfit(items: list[dict]) -> list[str]:
    """Pick the events an outfit fits. Conservative defaults:

      - "formal" / "business" tags → office cluster (office,
        business_meeting, interview). NOT formal_event — that's reserved
        for explicit black-tie cues.
      - "tuxedo" / "evening" / "black_tie" → formal_event ONLY.
      - "sport" / "athletic" / "athleisure" → gym.
      - "swim" / "beach" / "vacation" → beach.
      - "casual" / "minimal" → casual + home + date_night.
      - "outdoor" → casual + home (read as weekend hiking, not gym).

    Deliberately fires NO event if no tag matches and the outfit isn't
    a top+bottom+shoes triple — defaulting unknowns to "casual" trains
    the casual classifier on garbage and trains nothing else.
    """
    tags: set[str] = set()
    cats: set[str] = set()
    has_formal_collar = False
    for it in items:
        tags.update((it.get("style_tags") or []))
        c = it.get("category")
        if c: cats.add(c)
        if it.get("material") in {"polyester", "nylon", "lycra"}:
            tags.add("athletic")
        # Shawl / peak / notch collars are tuxedo / evening-jacket cues.
        if it.get("collar") in {"shawl", "peak"}:
            has_formal_collar = True
    has = lambda *xs: any(t in tags for t in xs)

    out: set[str] = set()

    # Athletic / sport
    if has("sport", "athletic", "athleisure", "gym", "performance"):
        out.add("gym")
    # Beach / swim
    if has("swim", "beach", "vacation"):
        out.add("beach")
    # Office: business attire that isn't black-tie
    if has("formal", "business", "office", "smart"):
        out.update({"office", "business_meeting", "interview"})
    # Formal-evening: ONLY explicit black-tie cues (or shawl/peak collar)
    if has("tuxedo", "black_tie", "evening") or has_formal_collar:
        out.add("formal_event")
    # Casual mid-formality
    if has("casual", "minimal", "smart_casual", "weekend"):
        out.update({"casual_outing", "home", "date_night"})
    # Outdoor reads as weekend casual, not gym
    if has("outdoor"):
        out.update({"casual_outing", "home"})

    # Fallback for outfits with no usable style cue
    if not out and (cats & {"top", "bottom", "shoes"}):
        out.update({"casual_outing", "home"})

    # Conflict guards
    if "formal_event" in out:
        out.discard("gym")
        out.discard("beach")
    if "office" in out:
        out.discard("gym")
        out.discard("beach")

    return sorted(out, key=EVENT_KEYS.index)


def train_occasion() -> None:
    print("\n── Stage 3: Occasion ──")
    while True:
        prog = _get("/training/v2/occasion/progress")
        total = prog["total_ratings"]
        if total >= TARGET_OCCASION:
            print(f"  ✓ {total} ratings (target {TARGET_OCCASION})")
            break
        print(f"  {total}/{TARGET_OCCASION}")
        try:
            batch = _get("/training/v2/occasion/next")
        except urllib.error.HTTPError as e:
            print(f"  /occasion/next HTTP {e.code}: {e.read().decode()[:120]}")
            return
        if batch.get("done"):
            print("  /occasion/next reports done, stopping")
            break
        ratings = []
        for o in batch["outfits"]:
            items = [oi.get("item") for oi in o.get("items", []) if oi.get("item")]
            ev = _events_for_outfit(items)
            ratings.append({"outfit_id": o["outfit_id"], "events_ok": ev})
        st, resp = _post("/training/v2/occasion/submit", {"ratings": ratings})
        if st != 200:
            print(f"  /occasion/submit HTTP {st}: {resp}")
            return
        print(f"  +{len(ratings)} ratings (events sample: {ratings[0]['events_ok']})")


def main() -> None:
    # Sanity check uvicorn is up + Tester is active
    try:
        prof = _get("/profiles")
    except Exception as e:
        sys.exit(f"uvicorn not reachable on {BASE}: {e}")
    active = prof.get("active") or "(none)"
    if active != "Tester":
        sys.exit(f"active profile is {active!r}; expected 'Tester'")
    print(f"Active profile: {active}")

    t0 = time.time()
    train_temperature()
    train_occasion()
    print(f"\nDone in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

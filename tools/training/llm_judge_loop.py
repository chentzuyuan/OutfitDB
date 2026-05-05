#!/usr/bin/env python3
"""LLM-judge training loop helper.

Usage:
  python llm_loop.py fetch [N]           Generate N outfit candidates across mixed contexts.
                                         Prints JSON to stdout for the LLM judge to evaluate.
  python llm_loop.py apply <ratings.json> Apply LLM ratings → POST /ratings → retrain.
  python llm_loop.py review [K]          Run /recommend across review contexts, return top-K
                                         outfits (default 5) as JSON for LLM review.
  python llm_loop.py status              Print current training state + last AUC.

The fetch step queries diverse contexts so the LLM evaluates across multiple
weather/occasion combinations in a single pass.
"""
import json
import sys
import requests
from pathlib import Path
from collections import Counter

BASE = "http://127.0.0.1:8000"

# Each cycle pulls candidates from these scenarios — diverse coverage matters
# more than depth for the LLM to detect bad combos across context types.
SCENARIOS = [
    ("2026-05-01T08:00:00", 32.0, 34, 28, "sunny",  "casual",  "Hot summer casual"),
    ("2026-05-01T09:00:00", 28.0, 30, 24, "sunny",  "sport",   "Hot day, gym"),
    ("2026-05-01T10:00:00", 24.0, 26, 20, "sunny",  "casual",  "Mild casual"),
    ("2026-05-01T11:00:00", 22.0, 25, 19, "cloudy", "work",    "Mild work"),
    ("2026-05-01T12:00:00", 20.0, 23, 17, "cloudy", "casual",  "Mild casual cloudy"),
    ("2026-05-01T13:00:00", 16.0, 19, 13, "cloudy", "work",    "Cool work"),
    ("2026-05-01T14:00:00", 14.0, 17, 11, "rainy",  "formal",  "Cold rainy formal"),
    ("2026-05-01T15:00:00", 10.0, 12, 7,  "cloudy", "formal",  "Cold formal"),
    ("2026-05-01T16:00:00", 8.0,  10, 5,  "cloudy", "casual",  "Cold casual"),
    ("2026-05-01T17:00:00", 18.0, 21, 15, "sunny",  "home",    "Mild home"),
]


def _ctx_for(date_iso, t, hi, lo, weather, occasion):
    r = requests.post(f"{BASE}/contexts/", json={
        "date": date_iso, "temperature": t, "temperature_high": hi,
        "temperature_low": lo, "weather": weather, "occasion": occasion,
    }, timeout=10).json()
    return r


def _outfit_summary(o):
    """Compact outfit description with all fields the LLM needs to judge fit."""
    items = []
    for oi in o.get("outfit_items", []):
        it = oi.get("item")
        if not it:
            continue
        item_dict = {
            "name": it["name"],
            "category": it["category"],
            "thickness": it["thickness"],
            "colors": it.get("colors") or [],
            "material": it.get("material"),
            "pattern": it.get("pattern", "solid"),
            "style_tags": it.get("style_tags") or [],
        }
        for opt in ("sleeve", "collar", "top_length", "pants_fit", "pants_length",
                    "shoulder_fit", "waist_fit"):
            if it.get(opt):
                item_dict[opt] = it[opt]
        items.append(item_dict)
    return {
        "outfit_id": o["id"],
        "warmth_score": o.get("warmth_score"),
        "optimal_layer_count": o.get("optimal_layer_count"),
        "items": items,
    }


def cmd_fetch(n_per_scenario=3):
    """Pull n candidates from each scenario, return JSON with full details."""
    p = requests.get(f"{BASE}/profiles", timeout=5).json()
    if p.get("active") != "Tester":
        print(json.dumps({"error": f"active={p.get('active')}, need Tester"}))
        sys.exit(1)
    out = []
    for date_iso, t, hi, lo, weather, occasion, label in SCENARIOS:
        ctx = _ctx_for(date_iso, t, hi, lo, weather, occasion)
        if "id" not in ctx:
            continue
        recs = requests.post(f"{BASE}/recommendations/", json={
            "context_id": ctx["id"], "top_k": n_per_scenario,
        }, timeout=60).json()
        if isinstance(recs, dict):
            continue
        for r in recs:
            out.append({
                "scenario": label,
                "context": {"temp_c": t, "weather": weather, "occasion": occasion},
                "outfit": _outfit_summary(r["outfit"]),
                "model_score": r.get("total_score"),
                "model_pref": r.get("preference_score"),
            })
    print(json.dumps({"batch": out}, ensure_ascii=False, indent=2))


def cmd_apply(ratings_path):
    """Read LLM ratings JSON, POST to /ratings, then retrain."""
    data = json.loads(Path(ratings_path).read_text())
    submitted = 0
    dist = Counter()
    for r in data.get("ratings", []):
        oid = r["outfit_id"]
        rating = max(-1, min(2, int(r["rating"])))
        rr = requests.post(f"{BASE}/ratings/", json={
            "outfit_id": oid,
            "rating": rating,
            "rating_source": "user_rated",
        }, timeout=10)
        if rr.status_code == 200:
            submitted += 1
            dist[rating] += 1
    tr = requests.post(f"{BASE}/train/", timeout=180).json()
    print(json.dumps({
        "submitted": submitted,
        "distribution": dict(dist),
        "training_samples": tr.get("training_samples"),
        "val_auc": tr.get("val_auc"),
        "val_logloss": tr.get("val_logloss"),
    }, ensure_ascii=False, indent=2))


def cmd_review(top_k=5):
    """For each review scenario, get top-K recommendations.
    Output is full outfit details so the LLM can flag any objections."""
    out = []
    for date_iso, t, hi, lo, weather, occasion, label in SCENARIOS:
        ctx = _ctx_for(date_iso, t, hi, lo, weather, occasion)
        if "id" not in ctx:
            continue
        recs = requests.post(f"{BASE}/recommendations/", json={
            "context_id": ctx["id"], "top_k": top_k,
        }, timeout=60).json()
        if isinstance(recs, dict):
            out.append({"scenario": label, "context": {"temp_c": t, "weather": weather,
                       "occasion": occasion}, "error": recs.get("detail")})
            continue
        out.append({
            "scenario": label,
            "context": {"temp_c": t, "weather": weather, "occasion": occasion},
            "top_k": [
                {**_outfit_summary(r["outfit"]),
                 "model_score": r.get("total_score"),
                 "model_pref": r.get("preference_score")}
                for r in recs
            ],
        })
    print(json.dumps({"review": out}, ensure_ascii=False, indent=2))


def cmd_status():
    p = requests.get(f"{BASE}/profiles").json()
    items = requests.get(f"{BASE}/items/").json()
    # Total ratings
    import sqlite3
    db = sqlite3.connect("/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/wardrobe.db")
    n_rat = db.execute("SELECT COUNT(*) FROM ratings").fetchone()[0]
    n_outfits = db.execute("SELECT COUNT(*) FROM outfits").fetchone()[0]
    n_runs = db.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
    last = db.execute(
        "SELECT val_auc, val_logloss, training_samples FROM model_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    db.close()
    print(json.dumps({
        "active_profile": p.get("active"),
        "items": len(items),
        "ratings": n_rat,
        "outfits": n_outfits,
        "model_runs": n_runs,
        "last_auc": last[0] if last else None,
        "last_logloss": last[1] if last else None,
        "last_samples": last[2] if last else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "fetch":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        cmd_fetch(n)
    elif cmd == "apply":
        cmd_apply(sys.argv[2])
    elif cmd == "review":
        k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        cmd_review(k)
    else:
        cmd_status()

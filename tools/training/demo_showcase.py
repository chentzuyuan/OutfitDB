#!/usr/bin/env python3
"""End-to-end demo: build 6 diverse contexts, run /recommendations on each,
print Top-3 for each so we can see what the trained model actually picks.
"""
import sys
import requests
from datetime import datetime

BASE = "http://127.0.0.1:8000"

# Use rolling timestamps to avoid context unique-by-day collisions
SCENARIOS = [
    ("2026-06-01T08:00:00", 28.0, 30, 24, "sunny",  "sport",   "Hot day, gym"),
    ("2026-06-02T09:00:00", 22.0, 25, 19, "sunny",  "casual",  "Mild casual"),
    ("2026-06-03T10:00:00", 22.0, 25, 19, "cloudy", "work",    "Mild work"),
    ("2026-06-04T11:00:00", 16.0, 19, 13, "cloudy", "work",    "Cool work"),
    ("2026-06-05T12:00:00", 14.0, 17, 11, "rainy",  "formal",  "Cold rainy formal"),
    ("2026-06-06T13:00:00", 8.0,  10, 5,  "cloudy", "casual",  "Cold casual"),
]

p = requests.get(f"{BASE}/profiles", timeout=5).json()
if p.get("active") != "Tester":
    print(f"ABORT: active={p.get('active')!r}, expected Tester"); sys.exit(1)

print(f"\n{'='*78}")
print(f"  OutfitDB demo  ·  Tester profile  ·  50 items, 753 training samples")
print(f"  Model: XGBoost @ profiles/Tester/models/current.json (Val AUC 0.72)")
print(f"{'='*78}\n")

for date_iso, t, hi, lo, weather, occasion, label in SCENARIOS:
    ctx = requests.post(f"{BASE}/contexts/", json={
        "date": date_iso, "temperature": t, "temperature_high": hi,
        "temperature_low": lo, "weather": weather, "occasion": occasion,
    }, timeout=10).json()
    if "id" not in ctx:
        print(f"  [{label}] ctx fail: {ctx}"); continue

    recs = requests.post(f"{BASE}/recommendations/", json={
        "context_id": ctx["id"], "top_k": 3,
    }, timeout=60).json()

    if isinstance(recs, dict):
        print(f"  ┃ {label:<24} {t:>5.1f}°C/{weather:<6}/{occasion:<7} ➜ {recs.get('detail','no recs')}\n")
        continue

    print(f"  ┏━ {label:<24} {t:>5.1f}°C  {weather}  {occasion}")
    for i, r in enumerate(recs, 1):
        o = r["outfit"]
        names = [it["item"]["name"] for it in o.get("outfit_items", []) if it.get("item")]
        print(f"  ┃  #{i}  layers={o['optimal_layer_count']}  warmth={o['warmth_score']:.1f}  "
              f"score={r['total_score']:.3f}  pref={r['preference_score']:.2f}")
        print(f"  ┃      {' + '.join(names)}")
    print(f"  ┗{'━' * 75}\n")

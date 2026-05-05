#!/usr/bin/env python3
"""Targeted negative campaign — drag down problem items in obviously wrong contexts.

For each (problem_item, wrong_context) pair, we use the recommendations
endpoint with must_include_item_ids to force outfits containing that item,
then bulk-rate them all -1. This is much faster than waiting for the
generator to randomly sample these combinations.
"""
import json
import requests
import sqlite3

BASE = "http://127.0.0.1:8000"
DB = "/Users/buttegg/School_Projects/wardrobe_env/profiles/Tester/wardrobe.db"

# (item_name, [bad_contexts])
TARGETS = [
    ("Beige Linen Jumpsuit", [
        ("2026-06-01T08:00:00", 32.0, "sunny", "sport"),    # jumpsuit not athletic
        ("2026-06-02T08:00:00", 14.0, "rainy", "formal"),   # jumpsuit not formal
        ("2026-06-03T08:00:00", 8.0,  "cloudy", "casual"),  # too thin for cold
        ("2026-06-04T08:00:00", 22.0, "cloudy", "work"),    # jumpsuit not office
    ]),
    ("Black Wool Peacoat", [
        ("2026-06-05T08:00:00", 32.0, "sunny", "casual"),
        ("2026-06-06T08:00:00", 28.0, "sunny", "sport"),
        ("2026-06-07T08:00:00", 24.0, "sunny", "casual"),
        ("2026-06-08T08:00:00", 22.0, "cloudy", "work"),
    ]),
    ("Black Down Puffer", [
        ("2026-06-09T08:00:00", 32.0, "sunny", "casual"),
        ("2026-06-10T08:00:00", 28.0, "sunny", "sport"),
        ("2026-06-11T08:00:00", 24.0, "sunny", "casual"),
        ("2026-06-12T08:00:00", 22.0, "cloudy", "work"),
    ]),
    ("Charcoal Wool Coat", [
        ("2026-06-13T08:00:00", 32.0, "sunny", "casual"),
        ("2026-06-14T08:00:00", 28.0, "sunny", "sport"),
        ("2026-06-15T08:00:00", 24.0, "sunny", "casual"),
    ]),
    ("Yellow Windbreaker", [
        ("2026-06-16T08:00:00", 14.0, "rainy", "formal"),
        ("2026-06-17T08:00:00", 10.0, "cloudy", "formal"),
        ("2026-06-18T08:00:00", 22.0, "cloudy", "work"),
    ]),
    ("White Tank Top", [
        ("2026-06-19T08:00:00", 14.0, "rainy", "formal"),
        ("2026-06-20T08:00:00", 10.0, "cloudy", "formal"),
        ("2026-06-21T08:00:00", 8.0,  "cloudy", "casual"),  # too thin for 8°C alone
    ]),
    ("Camel Trench Coat", [
        ("2026-06-22T08:00:00", 32.0, "sunny", "casual"),
        ("2026-06-23T08:00:00", 28.0, "sunny", "sport"),
    ]),
    ("Navy Herringbone Overcoat", [
        ("2026-06-24T08:00:00", 32.0, "sunny", "casual"),
        ("2026-06-25T08:00:00", 24.0, "sunny", "casual"),
    ]),
    ("Brown Leather Jacket", [
        ("2026-06-26T08:00:00", 14.0, "rainy", "formal"),
        ("2026-06-27T08:00:00", 10.0, "cloudy", "formal"),
    ]),
    ("Black Bomber Jacket", [
        ("2026-06-28T08:00:00", 14.0, "rainy", "formal"),
        ("2026-06-29T08:00:00", 10.0, "cloudy", "formal"),
        ("2026-06-30T08:00:00", 28.0, "sunny", "sport"),
    ]),
    ("Khaki Cargo Shorts", [
        ("2026-07-01T08:00:00", 22.0, "cloudy", "work"),
        ("2026-07-02T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-03T08:00:00", 10.0, "cloudy", "formal"),
    ]),
    ("Black Athletic Shorts", [
        ("2026-07-04T08:00:00", 22.0, "cloudy", "work"),
        ("2026-07-05T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-06T08:00:00", 10.0, "cloudy", "formal"),
    ]),
    ("Navy Basketball Shorts", [
        ("2026-07-07T08:00:00", 22.0, "cloudy", "work"),
        ("2026-07-08T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-09T08:00:00", 10.0, "cloudy", "formal"),
    ]),
    ("Black Joggers", [
        ("2026-07-10T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-11T08:00:00", 10.0, "cloudy", "formal"),
        ("2026-07-12T08:00:00", 22.0, "cloudy", "work"),
    ]),
    ("Light Wash Jeans", [
        ("2026-07-13T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-14T08:00:00", 10.0, "cloudy", "formal"),
    ]),
    ("White High-Top Sneakers", [
        ("2026-07-15T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-16T08:00:00", 10.0, "cloudy", "formal"),
    ]),
    ("Black Slip-On Sneakers", [
        ("2026-07-17T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-18T08:00:00", 10.0, "cloudy", "formal"),
    ]),
    ("Black Hoodie", [
        ("2026-07-19T08:00:00", 14.0, "rainy", "formal"),
        ("2026-07-20T08:00:00", 10.0, "cloudy", "formal"),
        ("2026-07-21T08:00:00", 22.0, "cloudy", "work"),
    ]),
]

def main():
    db = sqlite3.connect(DB)
    name_to_id = {r[0]: r[1] for r in db.execute("SELECT name, id FROM items").fetchall()}
    db.close()

    submitted = 0
    for item_name, contexts in TARGETS:
        item_id = name_to_id.get(item_name)
        if not item_id:
            print(f"  skip {item_name} (not in DB)")
            continue
        for date_iso, t, weather, occasion in contexts:
            # Create context
            ctx = requests.post(f"{BASE}/contexts/", json={
                "date": date_iso, "temperature": t,
                "temperature_high": t + 3, "temperature_low": t - 3,
                "weather": weather, "occasion": occasion,
            }, timeout=10).json()
            if "id" not in ctx:
                continue
            # Force-include this item, get top 3 outfits
            recs = requests.post(f"{BASE}/recommendations/", json={
                "context_id": ctx["id"], "top_k": 3,
                "must_include_item_ids": [item_id],
            }, timeout=60).json()
            if isinstance(recs, dict):
                continue
            for r in recs:
                rr = requests.post(f"{BASE}/ratings/", json={
                    "outfit_id": r["outfit"]["id"],
                    "rating": -1,
                    "rating_source": "user_rated",
                }, timeout=10)
                if rr.status_code == 200:
                    submitted += 1
            print(f"  {item_name} @ {t}°C {weather}/{occasion}: {len(recs)} outfits → -1")

    tr = requests.post(f"{BASE}/train/", timeout=180).json()
    print(f"\nSubmitted {submitted} negatives")
    print(f"Train: samples={tr.get('training_samples')} AUC={tr.get('val_auc'):.4f}")


if __name__ == "__main__":
    main()

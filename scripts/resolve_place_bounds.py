"""Resolve settlement centers and bounding boxes once with Nominatim.

Results stay in .cache and are validated against the current map positions.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import argparse
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.geocode import haversine_m, is_city_only_address  # noqa: E402
from src.roof_audit import _place_key  # noqa: E402
from src.state_store import load_state, map_records  # noqa: E402


def resolve(*, retry_unmatched: bool = False) -> None:
    groups = defaultdict(list)
    for rec in map_records(load_state()).values():
        if rec.get("lat") is not None and (rec.get("address_type") == "city" or is_city_only_address(rec.get("address") or "")):
            groups[_place_key(rec)].append(rec)
    out = ROOT / ".cache" / "place_bounds.json"
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    session = requests.Session()
    session.headers["User-Agent"] = "pipedrive-israel-map/1.1 (https://github.com/9108580/pipedrive-israel-map)"
    for i, (name, rows) in enumerate(sorted(groups.items()), 1):
        if name in results and (not retry_unmatched or results[name]["status"] == "matched"):
            continue
        old = (statistics.median(float(r["lat"]) for r in rows), statistics.median(float(r["lon"]) for r in rows))
        try:
            params = {"q": name if retry_unmatched else name + ", Israel",
                      "format": "jsonv2", "limit": 5, "addressdetails": 1}
            if not retry_unmatched:
                params["countrycodes"] = "il"
            response = session.get("https://nominatim.openstreetmap.org/search", params=params, timeout=30)
            response.raise_for_status()
            hits = response.json()
            choices = []
            for hit in hits:
                lat, lon = float(hit["lat"]), float(hit["lon"])
                distance = haversine_m(*old, lat, lon)
                if distance <= 4000 and hit.get("boundingbox"):
                    choices.append((distance, hit))
            if choices:
                _, best = min(choices, key=lambda x: x[0])
                south, north, west, east = map(float, best["boundingbox"])
                results[name] = {"status": "matched", "center": [float(best["lat"]), float(best["lon"])],
                                 "bbox": [west, south, east, north], "old_center": old,
                                 "distance_m": round(haversine_m(*old, float(best["lat"]), float(best["lon"]))) }
            else:
                results[name] = {"status": "unmatched", "old_center": old}
        except (requests.RequestException, ValueError, KeyError) as exc:
            print("lookup_error", i, repr(name), type(exc).__name__, flush=True)
            time.sleep(3)
            continue
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        if i % 25 == 0:
            print("resolved", i, "/", len(groups), "matched", sum(v["status"] == "matched" for v in results.values()), flush=True)
        time.sleep(1.1)
    print("done", len(results), "matched", sum(v["status"] == "matched" for v in results.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry-unmatched", action="store_true")
    resolve(retry_unmatched=parser.parse_args().retry_unmatched)

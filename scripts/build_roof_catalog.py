"""Build a compact roof catalog from Microsoft's open Israel footprint tiles.

Download links come from Microsoft's dataset-links.csv. The source tiles stay in
.cache; only a small, deterministic candidate index is committed for daily sync.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.geocode import haversine_m, is_city_only_address  # noqa: E402
from src.roof_audit import _place_key  # noqa: E402
from src.scatter import _area_m2, _near_polygon, _roof_point  # noqa: E402
from src.state_store import load_state, map_records  # noqa: E402

RELEASE = "2026-08-13"
SOURCE = "https://github.com/microsoft/GlobalMLBuildingFootprints"
CACHE = ROOT / ".cache"
CELL = 0.025
STREET_CELL = 0.003


def cell(lat: float, lon: float, size: float) -> tuple[int, int]:
    return math.floor(lat / size), math.floor(lon / size)


def nearby(index: dict[tuple[int, int], list], lat: float, lon: float, size: float):
    a, b = cell(lat, lon, size)
    for da in (-1, 0, 1):
        for db in (-1, 0, 1):
            yield from index.get((a + da, b + db), ())


def dense_roofs(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    index: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for p in points:
        index[cell(*p, 0.001)].append(p)
    dense = []
    for p in points:
        neighbors = 0
        for q in nearby(index, *p, 0.001):
            if q != p and haversine_m(*p, *q) <= 110:
                neighbors += 1
                if neighbors >= 3:
                    dense.append(p)
                    break
    return dense


def build() -> None:
    state = load_state()
    records = map_records(state)
    groups: dict[str, list[dict]] = defaultdict(list)
    streets: dict[str, tuple[float, float]] = {}
    street_places: dict[str, str] = {}
    for key, rec in records.items():
        if rec.get("lat") is None or rec.get("lon") is None:
            continue
        if rec.get("address_type") == "city" or is_city_only_address(rec.get("address") or ""):
            groups[_place_key(rec)].append(rec)
        else:
            streets[str(key)] = (float(rec["lat"]), float(rec["lon"]))
            street_places[str(key)] = _place_key(rec)

    old_centers = {name: (statistics.median(float(r["lat"]) for r in rows),
                          statistics.median(float(r["lon"]) for r in rows)) for name, rows in groups.items()}
    bounds_file = CACHE / "place_bounds.json"
    bounds = json.loads(bounds_file.read_text(encoding="utf-8")) if bounds_file.exists() else {}
    exclude_file = CACHE / "excluded_grounds.json"
    exclusions = json.loads(exclude_file.read_text(encoding="utf-8")) if exclude_file.exists() else {}
    supplement_file = CACHE / "osm_roof_supplements.json"
    osm_supplements = json.loads(supplement_file.read_text(encoding="utf-8")) if supplement_file.exists() else {}
    exclusion_boxes = {
        name: [(min(p[0] for p in polygon) - 0.0004,
                max(p[0] for p in polygon) + 0.0004,
                min(p[1] for p in polygon) - 0.0004,
                max(p[1] for p in polygon) + 0.0004, polygon)
               for polygon in polygons]
        for name, polygons in exclusions.items()
    }
    centers = {name: tuple(bounds[name]["center"]) if bounds.get(name, {}).get("status") == "matched"
               else old_centers[name] for name in groups}
    group_index: dict[tuple[int, int], list[str]] = defaultdict(list)
    for name, center in centers.items():
        group_index[cell(*center, CELL)].append(name)
    street_index: dict[tuple[int, int], list[str]] = defaultdict(list)
    for key, point in streets.items():
        street_index[cell(*point, STREET_CELL)].append(key)

    candidate_sets: dict[str, set[tuple[float, float]]] = defaultdict(set)
    street_best: dict[str, tuple[float, tuple[float, float]]] = {}
    seen = accepted = 0
    tiles = sorted(CACHE.glob("*.csv.gz"))
    if not tiles:
        raise SystemExit("Download Microsoft's Israel tiles listed in dataset-links.csv first")
    for tile in tiles:
        with gzip.open(tile, "rt", encoding="utf-8") as source:
            for line in source:
                seen += 1
                feature = json.loads(line)
                if float(feature.get("properties", {}).get("confidence") or 0) < 0.9:
                    continue
                try:
                    outer = feature["geometry"]["coordinates"][0]
                    rough_lon, rough_lat = outer[0][:2]
                except (IndexError, KeyError, TypeError):
                    continue
                close_groups = [name for name in nearby(group_index, rough_lat, rough_lon, CELL)
                                if haversine_m(rough_lat, rough_lon, *centers[name]) <= 2600]
                close_streets = [key for key in nearby(street_index, rough_lat, rough_lon, STREET_CELL)
                                 if haversine_m(rough_lat, rough_lon, *streets[key]) <= 200]
                if not close_groups and not close_streets:
                    continue
                polygon = [(float(p[1]), float(p[0])) for p in outer]
                area = _area_m2(polygon) if len(polygon) >= 4 else 0
                if area < 25 or area > 900:
                    continue
                roof = _roof_point(polygon)
                if roof is None:
                    continue
                roof = (round(roof[0], 7), round(roof[1], 7))
                accepted += 1
                for name in close_groups:
                    bounding = bounds.get(name, {})
                    bbox = bounding.get("bbox") if bounding.get("status") == "matched" else None
                    within_bounds = bbox is None or (bbox[1] - 0.0005 <= roof[0] <= bbox[3] + 0.0005
                                                     and bbox[0] - 0.0005 <= roof[1] <= bbox[2] + 0.0005)
                    if within_bounds and haversine_m(*roof, *centers[name]) <= 2200:
                        on_excluded_ground = any(south <= roof[0] <= north and west <= roof[1] <= east
                                                 and _near_polygon(*roof, polygon, 20)
                                                 for south, north, west, east, polygon in exclusion_boxes.get(name, ()))
                        if not on_excluded_ground:
                            candidate_sets[name].add(roof)
                for key in close_streets:
                    dist = haversine_m(*roof, *streets[key])
                    street_place = street_places[key]
                    on_excluded_ground = any(_near_polygon(*roof, ground, 20)
                                             for ground in exclusions.get(street_place, ()))
                    if not on_excluded_ground and dist <= 150 and (key not in street_best or dist < street_best[key][0]):
                        street_best[key] = (dist, roof)
        print(tile.name, "scanned", seen, "accepted", accepted, flush=True)

    catalog = {"source": SOURCE, "release": RELEASE, "places": {}}
    coverage = {}
    for name, rows in sorted(groups.items()):
        points = dense_roofs(sorted(candidate_sets[name]))
        source = "microsoft_footprint"
        if not points and osm_supplements.get(name):
            points = [tuple(p) for p in osm_supplements[name]]
            source = "osm_building"
        # Rank deterministically, then spread across 55m cells so one school
        # campus cannot dominate the sample used by the daily sync.
        points.sort(key=lambda p: hashlib.sha256(f"{name}:{p[0]}:{p[1]}".encode()).hexdigest())
        sampled: list[tuple[float, float]] = []
        used_cells: set[tuple[int, int]] = set()
        limit = max(120, min(600, len(rows) * 12))
        for p in points:
            bucket = cell(*p, 0.0005)
            if bucket in used_cells:
                continue
            used_cells.add(bucket)
            sampled.append(p)
            if len(sampled) >= limit:
                break
        catalog["places"][name] = {"center": centers[name], "roofs": sampled, "source": source}
        coverage[name] = {"systems": len(rows), "all_roofs": len(candidate_sets[name]),
                          "dense_roofs": len(points), "sampled_roofs": len(sampled)}

    (ROOT / "data" / "roof_candidates.json").write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (CACHE / "street_roof_matches.json").write_text(json.dumps({key: {"distance_m": round(d, 1), "roof": p} for key, (d, p) in street_best.items()}, separators=(",", ":")), encoding="utf-8")
    (CACHE / "roof_coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    print("places", len(groups), "with_candidates", sum(bool(v["sampled_roofs"]) for v in coverage.values()),
          "street_matches", len(street_best), "of", len(streets), "catalog_bytes", (ROOT / "data" / "roof_candidates.json").stat().st_size)


if __name__ == "__main__":
    build()

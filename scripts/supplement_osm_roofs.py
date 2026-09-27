"""Find residential OSM roofs for localities missing Microsoft footprints.

Reads the previously downloaded local PBF and writes only an ignored cache.
Run before build_roof_catalog.py when the source catalog has empty places.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / ".cache" / "osm-deps")]

import osmium  # noqa: E402

from src.geocode import haversine_m  # noqa: E402
from src.scatter import (  # noqa: E402
    _NON_HOME_TAGS, _RESIDENTIAL_TYPES, _SKIP_BUILDINGS,
    _area_m2, _filter_dense_cluster, _near_polygon, _roof_point,
)

CELL = 0.025


def cell(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / CELL), math.floor(lon / CELL)


class Buildings(osmium.SimpleHandler):
    def __init__(self, catalog: dict, bounds: dict, exclusions: dict) -> None:
        super().__init__()
        self.targets = {name: tuple(entry["center"]) for name, entry in catalog.items()
                        if not entry["roofs"] or entry.get("source") == "osm_building"}
        self.bounds = bounds
        self.exclusions = exclusions
        self.index: dict[tuple[int, int], list[str]] = defaultdict(list)
        for name, point in self.targets.items():
            self.index[cell(*point)].append(name)
        self.all_points: dict[str, set[tuple[float, float]]] = defaultdict(set)
        self.residential: dict[str, set[tuple[float, float]]] = defaultdict(set)

    def way(self, way) -> None:
        btype = (way.tags.get("building") or "").lower()
        if not btype or btype in _SKIP_BUILDINGS or not way.is_closed():
            return
        if any(way.tags.get(key) for key in _NON_HOME_TAGS):
            return
        try:
            polygon = [(node.location.lat, node.location.lon) for node in way.nodes]
        except osmium.InvalidLocationError:
            return
        point = _roof_point(polygon)
        if point is None or not 25 <= _area_m2(polygon) <= (3000 if btype in _RESIDENTIAL_TYPES else 900):
            return
        a, b = cell(*point)
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                for name in self.index.get((a + da, b + db), ()):
                    if haversine_m(*point, *self.targets[name]) > 2200:
                        continue
                    matched = self.bounds.get(name, {})
                    bbox = matched.get("bbox") if matched.get("status") == "matched" else None
                    if bbox and not (bbox[1] - .0005 <= point[0] <= bbox[3] + .0005 and
                                     bbox[0] - .0005 <= point[1] <= bbox[2] + .0005):
                        continue
                    if any(_near_polygon(*point, ground, 20) for ground in self.exclusions.get(name, ())):
                        continue
                    point = round(point[0], 7), round(point[1], 7)
                    self.all_points[name].add(point)
                    if btype in _RESIDENTIAL_TYPES:
                        self.residential[name].add(point)


def build() -> None:
    cache = ROOT / ".cache"
    source = cache / "israel-and-palestine.osm.pbf"
    if not source.exists():
        raise SystemExit(f"Missing local OSM PBF: {source}")
    catalog = json.loads((ROOT / "data" / "roof_candidates.json").read_text(encoding="utf-8"))["places"]
    bounds = json.loads((cache / "place_bounds.json").read_text(encoding="utf-8"))
    exclusions = json.loads((cache / "excluded_grounds.json").read_text(encoding="utf-8"))
    handler = Buildings(catalog, bounds, exclusions)
    handler.apply_file(str(source), locations=True)
    output = {}
    for name in handler.targets:
        all_points = sorted(handler.all_points[name])
        dense = _filter_dense_cluster(all_points, neighbor_m=110, min_neighbors=2, strict=True)
        output[name] = sorted(set(dense) | handler.residential[name])
    (cache / "osm_roof_supplements.json").write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    print("missing_places", len(output), "resolved", sum(bool(v) for v in output.values()),
          "roofs", sum(len(v) for v in output.values()))
    print(json.dumps({k: len(v) for k, v in output.items()}, ensure_ascii=True))


if __name__ == "__main__":
    build()

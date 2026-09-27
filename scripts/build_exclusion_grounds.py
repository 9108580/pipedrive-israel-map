"""Extract unsuitable grounds from the Geofabrik Israel/Palestine OSM PBF.

This one-pass extract avoids hundreds of rate-limited Overpass requests. The
source PBF and output polygons stay in .cache and are never published.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".cache" / "osm-deps"))

import osmium  # noqa: E402

from src.geocode import haversine_m, is_city_only_address  # noqa: E402
from src.roof_audit import _place_key  # noqa: E402
from src.state_store import load_state, map_records  # noqa: E402

SPORTS = {"pitch", "stadium", "sports_centre", "sports_hall", "track"}
INSTITUTIONS = {"school", "university", "college", "hospital", "place_of_worship"}
LANDUSE = {"industrial", "military", "cemetery", "construction"}
CELL = 0.04


def cell(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / CELL), math.floor(lon / CELL)


class Grounds(osmium.SimpleHandler):
    def __init__(self, centers: dict[str, tuple[float, float]]) -> None:
        super().__init__()
        self.centers = centers
        self.index: dict[tuple[int, int], list[str]] = defaultdict(list)
        for name, point in centers.items():
            self.index[cell(*point)].append(name)
        self.polygons: dict[str, list[list[list[float]]]] = defaultdict(list)
        self.matched_ways = 0

    def way(self, way) -> None:
        if way.tags.get("leisure") not in SPORTS and way.tags.get("amenity") not in INSTITUTIONS and way.tags.get("landuse") not in LANDUSE:
            return
        if not way.is_closed() or len(way.nodes) < 4:
            return
        try:
            polygon = [[round(node.location.lat, 7), round(node.location.lon, 7)] for node in way.nodes]
        except osmium.InvalidLocationError:
            return
        lat = statistics.mean(p[0] for p in polygon)
        lon = statistics.mean(p[1] for p in polygon)
        a, b = cell(lat, lon)
        self.matched_ways += 1
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                for name in self.index.get((a + da, b + db), ()):
                    if haversine_m(lat, lon, *self.centers[name]) <= 2800:
                        self.polygons[name].append(polygon)


def build() -> None:
    groups = defaultdict(list)
    for rec in map_records(load_state()).values():
        if rec.get("lat") is not None:
            groups[_place_key(rec)].append(rec)
    bounds_file = ROOT / ".cache" / "place_bounds.json"
    bounds = json.loads(bounds_file.read_text(encoding="utf-8")) if bounds_file.exists() else {}
    centers = {name: tuple(bounds[name]["center"]) if bounds.get(name, {}).get("status") == "matched"
               else (statistics.median(float(r["lat"]) for r in rows), statistics.median(float(r["lon"]) for r in rows))
               for name, rows in groups.items()}
    source = ROOT / ".cache" / "israel-and-palestine.osm.pbf"
    if not source.exists():
        raise SystemExit(f"Missing {source}; download from https://download.geofabrik.de/asia/israel-and-palestine.html")
    handler = Grounds(centers)
    handler.apply_file(str(source), locations=True)
    output = {name: handler.polygons.get(name, []) for name in centers}
    path = ROOT / ".cache" / "excluded_grounds.json"
    path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("localities", len(output), "matched_ways", handler.matched_ways,
          "assigned_polygons", sum(len(x) for x in output.values()), "bytes", path.stat().st_size)


if __name__ == "__main__":
    build()

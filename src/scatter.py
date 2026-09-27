"""Snap / scatter points onto residential buildings via Overpass."""

from __future__ import annotations

import logging
import math
import random
import time
import hashlib
import json
from pathlib import Path
from typing import Sequence

import requests

from . import config
from .geocode import haversine_m

log = logging.getLogger(__name__)

OVERPASS_MIRRORS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

_SKIP_BUILDINGS = {
    "garage", "garages", "carport", "shed", "hut", "roof", "barn", "farm",
    "greenhouse", "industrial", "warehouse", "construction", "ruins",
    "collapsed", "service", "toilet", "kiosk", "cabin", "container",
    "stadium", "sports_hall", "grandstand", "school", "university",
    "hospital", "church", "mosque", "synagogue", "temple", "public",
    "government", "commercial", "retail", "supermarket", "train_station",
}

_NON_HOME_TAGS = {"amenity", "shop", "tourism", "office", "industrial", "military", "sport", "leisure", "healthcare"}
_RESIDENTIAL_TYPES = {"house", "residential", "apartments", "detached", "semidetached_house", "terrace", "bungalow"}


class NoSuitableBuilding(RuntimeError):
    """An approximate location cannot be put on a verified roof."""


class BuildingLookupError(NoSuitableBuilding):
    """OSM could not be reached; retry instead of treating data as empty."""


def _inside(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    hit = False
    for (alat, alon), (blat, blon) in zip(polygon, polygon[1:]):
        if (alat > lat) != (blat > lat) and lon < (blon - alon) * (lat - alat) / (blat - alat) + alon:
            hit = not hit
    return hit


def _roof_point(polygon: list[tuple[float, float]]) -> tuple[float, float] | None:
    if len(polygon) < 4:
        return None
    origin_lat, origin_lon = polygon[0]
    cross_sum = x_sum = y_sum = 0.0
    for (alat, alon), (blat, blon) in zip(polygon, polygon[1:]):
        ax, ay = alon - origin_lon, alat - origin_lat
        bx, by = blon - origin_lon, blat - origin_lat
        cross = ax * by - bx * ay
        cross_sum += cross
        x_sum += (ax + bx) * cross
        y_sum += (ay + by) * cross
    if abs(cross_sum) > 1e-12:
        p = (origin_lat + y_sum / (3 * cross_sum), origin_lon + x_sum / (3 * cross_sum))
        if _inside(*p, polygon):
            return p
    lats, lons = [p[0] for p in polygon], [p[1] for p in polygon]
    for steps in (3, 7, 15):
        for i in range(1, steps):
            for j in range(1, steps):
                p = (min(lats) + (max(lats) - min(lats)) * i / steps,
                     min(lons) + (max(lons) - min(lons)) * j / steps)
                if _inside(*p, polygon):
                    return p
    return None


def _area_m2(polygon: list[tuple[float, float]]) -> float:
    middle_lat = sum(p[0] for p in polygon) / len(polygon)
    scale = 111_320 * math.cos(math.radians(middle_lat)) * 111_320 / 2
    origin_lat, origin_lon = polygon[0]
    return abs(sum((a[1] - origin_lon) * (b[0] - origin_lat) -
                   (b[1] - origin_lon) * (a[0] - origin_lat)
                   for a, b in zip(polygon, polygon[1:]))) * scale


def _near_polygon(lat: float, lon: float, polygon: list[tuple[float, float]], buffer_m: float) -> bool:
    if _inside(lat, lon, polygon):
        return True
    sx = 111_320 * math.cos(math.radians(lat))
    for (alat, alon), (blat, blon) in zip(polygon, polygon[1:]):
        ax, ay = (alon - lon) * sx, (alat - lat) * 111_320
        bx, by = (blon - lon) * sx, (blat - lat) * 111_320
        vx, vy = bx - ax, by - ay
        t = max(0.0, min(1.0, -(ax * vx + ay * vy) / (vx * vx + vy * vy))) if vx * vx + vy * vy else 0.0
        if math.hypot(ax + t * vx, ay + t * vy) <= buffer_m:
            return True
    return False


class ResidentialScatter:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "pipedrive-israel-map/1.1 (https://github.com/9108580/pipedrive-israel-map)", "Accept": "application/json"}
        )
        self._last_call = 0.0
        self._cache: dict[str, list[tuple[float, float]]] = {}
        self.cache_dir = cache_dir

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = max(config.OVERPASS_MIN_INTERVAL, 1.5) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _query_overpass(self, query: str) -> list[dict]:
        """Fetch OSM geometry; failed lookups must never turn into map points."""
        cache_file = self.cache_dir / (hashlib.sha256(query.encode()).hexdigest() + ".json") if self.cache_dir else None
        if cache_file and cache_file.exists() and time.time() - cache_file.stat().st_mtime < 7 * 86400:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        last_exc: Exception | None = None
        for url in OVERPASS_MIRRORS:
            self._throttle()
            try:
                r = self.session.get(url, params={"data": query}, timeout=60)
                if r.status_code in (429, 502, 503, 504):
                    wait = 3 if r.status_code == 429 else 1
                    log.warning(
                        "Overpass HTTP %s (%s), sleep %ss",
                        r.status_code,
                        url.split("/")[2],
                        wait,
                    )
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                elements = (r.json() or {}).get("elements") or []
                if cache_file:
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(elements), encoding="utf-8")
                return elements
            except Exception as exc:
                last_exc = exc
                log.warning("Overpass failed (%s): %s", url.split("/")[2], exc)
                time.sleep(1)
        raise BuildingLookupError(f"Overpass unavailable: {last_exc}")

    def fetch_buildings(
        self, lat: float, lon: float, radius_m: int = 1500
    ) -> list[tuple[float, float]]:
        key = f"{lat:.4f},{lon:.4f},{radius_m}"
        if key in self._cache:
            return self._cache[key]

        # Include footprints and sports grounds; the old 'out center 200'
        # returned arbitrary early ways and could put a pin beside a pitch.
        query = (
            f'[out:json][timeout:35];(way["building"](around:{radius_m},{lat},{lon});'
            f'way["leisure"~"^(pitch|stadium|sports_centre|sports_hall|track)$"]'
            f'(around:{radius_m},{lat},{lon});'
            f'way["amenity"~"^(school|university|college|hospital|place_of_worship)$"]'
            f'(around:{radius_m},{lat},{lon}););out geom;'
        )
        elements = self._query_overpass(query)
        excluded_grounds = [[(float(p["lat"]), float(p["lon"])) for p in e.get("geometry", [])]
                            for e in elements if (e.get("tags") or {}).get("leisure") in {"pitch", "stadium", "sports_centre", "sports_hall", "track"}
                            or (e.get("tags") or {}).get("amenity") in {"school", "university", "college", "hospital", "place_of_worship"}]
        excluded_grounds = [p for p in excluded_grounds if len(p) >= 4]

        points: list[tuple[float, float]] = []
        seen: set[tuple[float, float]] = set()
        for el in elements:
            tags = el.get("tags") or {}
            btype = (tags.get("building") or "").lower()
            if not btype or btype in _SKIP_BUILDINGS or any(tags.get(k) for k in _NON_HOME_TAGS):
                continue
            polygon = [(float(p["lat"]), float(p["lon"])) for p in el.get("geometry", [])]
            pt = _roof_point(polygon)
            if pt is None or _area_m2(polygon) > (3000 if btype in _RESIDENTIAL_TYPES else 900):
                continue
            if any(_near_polygon(*pt, ground, 30) for ground in excluded_grounds):
                continue
            if pt not in seen:
                seen.add(pt)
                points.append(pt)

        # Keep all eligible roofs here. City-only picks apply the extra density
        # gate; precise street addresses may genuinely be isolated houses.
        self._cache[key] = points
        return points

    def fetch_buildings_expanding(
        self, lat: float, lon: float, *, desired: int = 15
    ) -> list[tuple[float, float]]:
        best: list[tuple[float, float]] = []
        for radius in (900, 1500, 2500):
            pts = self.fetch_buildings(lat, lon, radius_m=radius)
            if len(pts) > len(best):
                best = pts
            if len(_filter_dense_cluster(pts, neighbor_m=110.0, min_neighbors=2)) >= desired:
                return pts
        return best

    def pick_point(
        self,
        lat: float,
        lon: float,
        occupied: Sequence[tuple[float, float]],
        seed: str | int,
        candidates: Sequence[tuple[float, float]] | None = None,
    ) -> tuple[float, float]:
        pool_all = list(candidates) if candidates is not None else self.fetch_buildings_expanding(lat, lon)
        pool = _filter_dense_cluster(pool_all, neighbor_m=110.0, min_neighbors=2, strict=True)
        if not pool:
            raise NoSuitableBuilding(f"No residential roof cluster near {lat:.5f},{lon:.5f}")

        rng = random.Random(str(seed))
        order = list(range(len(pool)))
        rng.shuffle(order)
        for i in order:
            clat, clon = pool[i]
            if all(haversine_m(clat, clon, *o) > 12 for o in occupied):
                return clat, clon
        # More systems than mapped roofs may share a real roof. Never make a
        # synthetic offset that lands on the pitch, road, or open terrain.
        return pool[order[0]]

    def snap_to_building(
        self,
        lat: float,
        lon: float,
        occupied: Sequence[tuple[float, float]],
        seed: str | int,
        max_snap_m: float = 150.0,
        candidates: Sequence[tuple[float, float]] | None = None,
    ) -> tuple[float, float]:
        pool_all = list(candidates) if candidates is not None else self.fetch_buildings(lat, lon, radius_m=500)
        pool = pool_all
        nearby = [
            (clat, clon)
            for clat, clon in pool
            if haversine_m(lat, lon, clat, clon) <= max_snap_m
        ]
        if not nearby:
            raise NoSuitableBuilding(f"No verified roof within {max_snap_m:g}m of address")
        # Prefer denser-looking points: more neighbors within 80m
        def density(p: tuple[float, float]) -> tuple[int, float]:
            n = sum(1 for q in pool_all if haversine_m(p[0], p[1], q[0], q[1]) < 80)
            dist = haversine_m(lat, lon, p[0], p[1])
            return (-n, dist)

        use_sorted = sorted(nearby, key=density)
        for clat, clon in use_sorted:
            if _far_enough(clat, clon, occupied):
                return clat, clon
        return use_sorted[0]


def _filter_dense_cluster(
    points: list[tuple[float, float]],
    neighbor_m: float = 90.0,
    min_neighbors: int = 2,
    strict: bool = False,
) -> list[tuple[float, float]]:
    """Keep buildings that sit in a fabric of neighbors (skip lonely field sheds)."""
    if len(points) < 3:
        return [] if strict else points
    kept: list[tuple[float, float]] = []
    for i, p in enumerate(points):
        n = 0
        for j, q in enumerate(points):
            if i == j:
                continue
            if haversine_m(p[0], p[1], q[0], q[1]) <= neighbor_m:
                n += 1
                if n >= min_neighbors:
                    kept.append(p)
                    break
    return kept if strict else (kept or points)


def _far_enough(
    lat: float, lon: float, occupied: Sequence[tuple[float, float]]
) -> bool:
    for olat, olon in occupied:
        if haversine_m(lat, lon, olat, olon) < config.MIN_POINT_DISTANCE_M:
            return False
    return True

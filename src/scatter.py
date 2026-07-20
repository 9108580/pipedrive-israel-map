"""Snap / scatter points onto residential buildings via Overpass."""

from __future__ import annotations

import logging
import random
import time
from collections import defaultdict
from typing import Sequence

import requests

from . import config
from .geocode import haversine_m

log = logging.getLogger(__name__)

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

_SKIP_BUILDINGS = {
    "garage", "garages", "carport", "shed", "hut", "roof", "barn", "farm",
    "greenhouse", "industrial", "warehouse", "construction", "ruins",
    "collapsed", "service", "toilet", "kiosk", "cabin", "container",
}


class ResidentialScatter:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
        )
        self._last_call = 0.0
        self._cache: dict[str, list[tuple[float, float]]] = {}

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = max(config.OVERPASS_MIN_INTERVAL, 1.5) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _query_overpass(self, query: str) -> list[dict] | None:
        """Try each mirror once. Returns None if all mirrors failed."""
        last_exc: Exception | None = None
        for url in OVERPASS_MIRRORS:
            self._throttle()
            try:
                r = self.session.post(url, data={"data": query}, timeout=45)
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
                return (r.json() or {}).get("elements") or []
            except Exception as exc:
                last_exc = exc
                log.warning("Overpass failed (%s): %s", url.split("/")[2], exc)
                time.sleep(1)
        if last_exc:
            log.warning("Overpass gave up: %s", last_exc)
        return None

    def fetch_buildings(
        self, lat: float, lon: float, radius_m: int = 900
    ) -> list[tuple[float, float]]:
        key = f"{lat:.4f},{lon:.4f},{radius_m}"
        if key in self._cache:
            return self._cache[key]

        # Lighter query: ways only, capped, short timeout
        query = f"""
        [out:json][timeout:25];
        way["building"](around:{radius_m},{lat},{lon});
        out center 200;
        """
        elements = self._query_overpass(query)
        if elements is None:
            # Do not cache transport failures — allow retry later
            log.info(
                "Overpass unavailable near %.4f,%.4f r=%sm",
                lat, lon, radius_m,
            )
            return []

        points: list[tuple[float, float]] = []
        seen: set[tuple[float, float]] = set()
        for el in elements:
            tags = el.get("tags") or {}
            btype = (tags.get("building") or "").lower()
            if btype in _SKIP_BUILDINGS:
                continue
            if "lat" in el and "lon" in el:
                pt = (round(float(el["lat"]), 6), round(float(el["lon"]), 6))
            elif "center" in el:
                c = el["center"]
                pt = (round(float(c["lat"]), 6), round(float(c["lon"]), 6))
            else:
                continue
            if pt not in seen:
                seen.add(pt)
                points.append(pt)

        dense = _filter_dense_cluster(points, neighbor_m=90.0, min_neighbors=2)
        if len(dense) >= 8:
            points = dense
            log.info(
                "Overpass buildings near %.4f,%.4f r=%sm -> %s dense",
                lat, lon, radius_m, len(points),
            )
        else:
            log.info(
                "Overpass buildings near %.4f,%.4f r=%sm -> %s (low density)",
                lat, lon, radius_m, len(points),
            )

        # Cache real answers only (including genuine empty)
        self._cache[key] = points
        return points

    def fetch_buildings_expanding(
        self, lat: float, lon: float
    ) -> list[tuple[float, float]]:
        best: list[tuple[float, float]] = []
        for radius in (500, 900, 1500, 2500):
            pts = self.fetch_buildings(lat, lon, radius_m=radius)
            if len(pts) > len(best):
                best = pts
            if len(pts) >= 15:
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
        pool = list(candidates) if candidates is not None else self.fetch_buildings_expanding(lat, lon)
        if not pool:
            log.warning("No OSM buildings near %.5f,%.5f — keeping center", lat, lon)
            return lat, lon

        rng = random.Random(str(seed))
        order = list(range(len(pool)))
        rng.shuffle(order)
        for i in order:
            clat, clon = pool[i]
            if _far_enough(clat, clon, occupied):
                return clat, clon

        best = pool[0]
        best_score = -1.0
        for clat, clon in pool:
            if not occupied:
                return clat, clon
            nearest = min(haversine_m(clat, clon, o[0], o[1]) for o in occupied)
            if nearest > best_score:
                best_score = nearest
                best = (clat, clon)
        return best

    def snap_to_building(
        self,
        lat: float,
        lon: float,
        occupied: Sequence[tuple[float, float]],
        seed: str | int,
        max_snap_m: float = 150.0,
        candidates: Sequence[tuple[float, float]] | None = None,
    ) -> tuple[float, float]:
        pool_all = list(candidates) if candidates is not None else self.fetch_buildings_expanding(lat, lon)
        if not pool_all:
            return lat, lon
        # Never keep the exact same empty-field coordinate even if OSM lists it
        pool = [
            (clat, clon)
            for clat, clon in pool_all
            if haversine_m(lat, lon, clat, clon) > 5.0
        ] or pool_all
        nearby = [
            (clat, clon)
            for clat, clon in pool
            if haversine_m(lat, lon, clat, clon) <= max_snap_m
        ]
        use = nearby or pool
        # Prefer denser-looking points: more neighbors within 80m
        def density(p: tuple[float, float]) -> tuple[int, float]:
            n = sum(1 for q in pool_all if haversine_m(p[0], p[1], q[0], q[1]) < 80)
            dist = haversine_m(lat, lon, p[0], p[1])
            return (-n, dist)

        use_sorted = sorted(use, key=density)
        for clat, clon in use_sorted:
            if _far_enough(clat, clon, occupied):
                return clat, clon
        return self.pick_point(lat, lon, occupied, seed=seed, candidates=pool_all)


def _filter_dense_cluster(
    points: list[tuple[float, float]],
    neighbor_m: float = 90.0,
    min_neighbors: int = 2,
) -> list[tuple[float, float]]:
    """Keep buildings that sit in a fabric of neighbors (skip lonely field sheds)."""
    if len(points) < 5:
        return points
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
    return kept or points


def _far_enough(
    lat: float, lon: float, occupied: Sequence[tuple[float, float]]
) -> bool:
    for olat, olon in occupied:
        if haversine_m(lat, lon, olat, olon) < config.MIN_POINT_DISTANCE_M:
            return False
    return True
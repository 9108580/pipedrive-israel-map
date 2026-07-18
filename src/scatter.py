"""Scatter city-level points near residential buildings via Overpass."""

from __future__ import annotations

import logging
import random
import time
from typing import Sequence

import requests

from . import config
from .geocode import haversine_m

log = logging.getLogger(__name__)


class ResidentialScatter:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "application/json",
            }
        )
        self._last_call = 0.0
        self._cache: dict[str, list[tuple[float, float]]] = {}

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = config.OVERPASS_MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def fetch_buildings(
        self, lat: float, lon: float, radius_m: int = 1200
    ) -> list[tuple[float, float]]:
        key = f"{lat:.4f},{lon:.4f},{radius_m}"
        if key in self._cache:
            return self._cache[key]

        query = f"""
        [out:json][timeout:60];
        (
          way["building"~"house|residential|apartments|detached|semidetached_house|terrace"](around:{radius_m},{lat},{lon});
          node["building"~"house|residential|apartments"](around:{radius_m},{lat},{lon});
        );
        out center 80;
        """
        self._throttle()
        points: list[tuple[float, float]] = []
        try:
            r = self.session.post(
                config.OVERPASS_URL,
                data={"data": query},
                timeout=90,
            )
            r.raise_for_status()
            data = r.json()
            for el in data.get("elements") or []:
                if "lat" in el and "lon" in el:
                    points.append((float(el["lat"]), float(el["lon"])))
                elif "center" in el:
                    c = el["center"]
                    points.append((float(c["lat"]), float(c["lon"])))
        except Exception as exc:
            log.warning("Overpass failed near %s,%s: %s", lat, lon, exc)

        if not points:
            # Fallback: small random offsets around center (still near "main" address)
            rng = random.Random(f"{lat},{lon}")
            for _ in range(40):
                # ~50–400 m
                dlat = rng.uniform(-0.0035, 0.0035)
                dlon = rng.uniform(-0.0035, 0.0035)
                points.append((lat + dlat, lon + dlon))

        self._cache[key] = points
        return points

    def pick_point(
        self,
        lat: float,
        lon: float,
        occupied: Sequence[tuple[float, float]],
        seed: str | int,
    ) -> tuple[float, float]:
        candidates = self.fetch_buildings(lat, lon)
        rng = random.Random(str(seed))
        order = list(range(len(candidates)))
        rng.shuffle(order)

        for i in order:
            clat, clon = candidates[i]
            if _far_enough(clat, clon, occupied):
                return clat, clon

        # Last resort: random offsets from center (~80–500 m)
        import math

        cos_lat = max(0.2, abs(math.cos(math.radians(lat))))
        for n in range(1, 50):
            angle = rng.random() * 2 * math.pi
            meters = config.MIN_POINT_DISTANCE_M * (0.8 + n * 0.35)
            dlat = (meters * math.cos(angle)) / 111_320.0
            dlon = (meters * math.sin(angle)) / (111_320.0 * cos_lat)
            clat, clon = lat + dlat, lon + dlon
            if _far_enough(clat, clon, occupied):
                return clat, clon

        return lat + rng.uniform(-0.001, 0.001), lon + rng.uniform(-0.001, 0.001)


def _far_enough(
    lat: float, lon: float, occupied: Sequence[tuple[float, float]]
) -> bool:
    for olat, olon in occupied:
        if haversine_m(lat, lon, olat, olon) < config.MIN_POINT_DISTANCE_M:
            return False
    return True

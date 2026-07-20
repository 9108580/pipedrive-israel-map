"""Re-snap mapped points onto residential OSM buildings (resumable)."""
from __future__ import annotations

import argparse
import logging
import random
import re

from src.geocode import haversine_m
from src.scatter import ResidentialScatter, _far_enough
from src.state_store import load_state, place_label, save_state, write_geojson

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("resnap")

HE_RE = re.compile(r"[\u0590-\u05FF]")


def settlement_key(rec: dict) -> str:
    """Group by village/town, not regional council."""
    place = place_label(rec)
    if place and "מועצה" not in place:
        return place.strip().lower()
    disp = (rec.get("geocode_display") or "").strip()
    if disp:
        first = disp.split(",")[0].strip()
        if first and "מועצה" not in first:
            return first.lower()
    ck = (rec.get("city_key") or "unknown").strip()
    if "מועצה" in ck and disp:
        return disp.split(",")[0].strip().lower() or ck.lower()
    return ck.lower() or "unknown"


def search_center(group: list[tuple[str, dict]]) -> tuple[float, float]:
    """Prefer Hebrew settlement geocode display centroid of points in group."""
    lats = [float(r["lat"]) for _, r in group]
    lons = [float(r["lon"]) for _, r in group]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def resnap(*, force: bool = False, limit: int | None = None) -> None:
    state = load_state()
    persons = state.get("persons") or {}
    scatter = ResidentialScatter()

    items = sorted(
        ((pid, rec) for pid, rec in persons.items() if rec.get("lat") is not None),
        key=lambda x: int(x[1].get("project_number") or 0),
    )
    if not force:
        items = [(p, r) for p, r in items if not r.get("snapped_to_building")]
    if limit is not None:
        items = items[:limit]

    by_place: dict[str, list[tuple[str, dict]]] = {}
    for pid, rec in items:
        key = settlement_key(rec)
        by_place.setdefault(key, []).append((pid, rec))

    done = 0
    total = len(items)
    skipped_cities = 0
    for place_key, group in sorted(by_place.items(), key=lambda x: x[0]):
        occupied: list[tuple[float, float]] = []
        # Keep already-snapped points in same settlement as occupied (non-force)
        if not force:
            for rec in persons.values():
                if settlement_key(rec) != place_key:
                    continue
                if rec.get("lat") is None:
                    continue
                if rec.get("snapped_to_building"):
                    occupied.append((float(rec["lat"]), float(rec["lon"])))

        clat, clon = search_center(group)
        buildings: list[tuple[float, float]] = []
        for radius in (700, 1200, 2000, 3500, 5000):
            buildings = scatter.fetch_buildings(clat, clon, radius_m=radius)
            if len(buildings) >= max(15, len(group)):
                break
        if not buildings:
            log.warning("No buildings for %s (%s pts) — skip", place_key[:50], len(group))
            skipped_cities += 1
            for _, rec in group:
                rec["snapped_to_building"] = False
            continue

        pool = buildings[:]
        random.Random(place_key).shuffle(pool)

        for pid, rec in group:
            old_lat, old_lon = float(rec["lat"]), float(rec["lon"])
            candidates = sorted(
                pool, key=lambda p: haversine_m(old_lat, old_lon, p[0], p[1])
            )
            chosen = None
            for b in candidates:
                if _far_enough(b[0], b[1], occupied):
                    chosen = b
                    break
            if chosen is None:
                chosen = candidates[0]
            rec["lat"], rec["lon"] = chosen[0], chosen[1]
            rec["snapped_to_building"] = True
            occupied.append(chosen)
            done += 1

        save_state(state)
        write_geojson(state)
        log.info(
            "Place %s: %s pts / %s buildings (%s/%s)",
            place_key[:40],
            len(group),
            len(buildings),
            done,
            total,
        )

    save_state(state)
    out = write_geojson(state)
    snapped = sum(1 for v in persons.values() if v.get("snapped_to_building"))
    log.info(
        "Resnap done moved=%s total_snapped=%s skipped_places=%s -> %s",
        done,
        snapped,
        skipped_cities,
        out,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    resnap(force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
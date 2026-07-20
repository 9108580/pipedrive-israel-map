"""Re-snap mapped points onto dense residential OSM buildings."""
from __future__ import annotations

import argparse
import logging
import re

from src.scatter import ResidentialScatter
from src.state_store import load_state, place_label, save_state, write_geojson

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("resnap")


def settlement_key(rec: dict) -> str:
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
        by_place.setdefault(settlement_key(rec), []).append((pid, rec))

    done = 0
    total = len(items)
    for place_key, group in sorted(by_place.items(), key=lambda x: x[0]):
        occupied: list[tuple[float, float]] = []
        if not force:
            for rec in persons.values():
                if settlement_key(rec) != place_key or rec.get("lat") is None:
                    continue
                if rec.get("snapped_to_building"):
                    occupied.append((float(rec["lat"]), float(rec["lon"])))

        clat = sum(float(r["lat"]) for _, r in group) / len(group)
        clon = sum(float(r["lon"]) for _, r in group) / len(group)

        # Warm dense building cache for settlement
        buildings = scatter.fetch_buildings_expanding(clat, clon)
        if not buildings:
            log.warning("No buildings for %s (%s pts)", place_key[:50], len(group))
            for _, rec in group:
                rec["snapped_to_building"] = False
            continue

        for pid, rec in group:
            old_lat, old_lon = float(rec["lat"]), float(rec["lon"])
            atype = rec.get("address_type") or "city"
            if atype == "street":
                new_lat, new_lon = scatter.snap_to_building(
                    old_lat, old_lon, occupied, seed=pid, candidates=buildings
                )
            else:
                new_lat, new_lon = scatter.pick_point(
                    clat, clon, occupied, seed=pid, candidates=buildings
                )
            rec["lat"], rec["lon"] = new_lat, new_lon
            rec["snapped_to_building"] = True
            occupied.append((new_lat, new_lon))
            done += 1

        save_state(state)
        write_geojson(state)
        log.info("Place %s: %s pts / %s buildings (%s/%s)", place_key[:40], len(group), len(buildings), done, total)

    save_state(state)
    out = write_geojson(state)
    snapped = sum(1 for v in persons.values() if v.get("snapped_to_building"))
    log.info("Resnap done moved=%s total_snapped=%s -> %s", done, snapped, out)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    resnap(force=args.force, limit=args.limit)


if __name__ == "__main__":
    main()
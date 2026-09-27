"""Audit and repair every published pin against OSM roof footprints.

Dry run by default. The Pipedrive address and project number never change.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import config
from .geocode import haversine_m, is_city_only_address
from .roof_catalog import load_catalog, pick_nearest_roof, pick_roof, resolve_place_key
from .scatter import NoSuitableBuilding
from .state_store import load_state, map_records, place_label, save_state, write_geojson

log = logging.getLogger(__name__)


def _place_key(rec: dict[str, Any]) -> str:
    city = str(rec.get("city_key") or "").strip().casefold()
    fallback = city if city and city not in {"israel", "ישראל", "palestine", "unknown"} and "מועצה" not in city else place_label(rec)
    return resolve_place_key(rec.get("address") or "", fallback, rec.get("geocode_display") or "")


def audit_roofs(*, apply: bool = False, place: str | None = None, max_groups: int | None = None) -> dict[str, Any]:
    state = load_state()
    records = map_records(state)
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for key, rec in records.items():
        if rec.get("lat") is not None and rec.get("lon") is not None:
            grouped[_place_key(rec)].append((key, rec))
    groups = sorted(grouped.items())
    if place:
        groups = [(name, rows) for name, rows in groups if place.casefold() in name or any(place.casefold() in place_label(r).casefold() for _, r in rows)]
    if max_groups is not None:
        groups = groups[:max_groups]

    catalog = load_catalog().get("places", {})
    street_match_file = config.ROOT / ".cache" / "street_roof_matches.json"
    street_matches = json.loads(street_match_file.read_text(encoding="utf-8")) if street_match_file.exists() else {}
    stats: dict[str, Any] = {"groups": len(groups), "records": 0, "city": 0, "street": 0, "moved": 0, "unchanged": 0, "unresolved": 0}
    changes: list[dict[str, Any]] = []
    for index, (name, rows) in enumerate(groups, 1):
        center = (statistics.median(float(r["lat"]) for _, r in rows),
                  statistics.median(float(r["lon"]) for _, r in rows))
        occupied: list[tuple[float, float]] = []
        ordered = sorted(rows, key=lambda item: (item[1].get("address_type") == "city", int(item[1].get("project_number") or 0)))
        for key, rec in ordered:
            old = (float(rec["lat"]), float(rec["lon"]))
            approximate = rec.get("address_type") == "city" or is_city_only_address(rec.get("address") or "")
            stats["records"] += 1
            stats["city" if approximate else "street"] += 1
            try:
                if approximate:
                    if name not in catalog:
                        raise NoSuitableBuilding("No footprint catalog for settlement")
                    new = pick_roof(name, occupied, seed=key)
                    source = catalog[name].get("source", "microsoft_footprint")
                else:
                    match = street_matches.get(str(key))
                    if match and match.get("distance_m", 1000) <= 150:
                        new = tuple(match["roof"])
                        source = "microsoft_footprint"
                    else:
                        new = pick_nearest_roof(name, old, occupied)
                        source = catalog[name].get("source", "microsoft_footprint")
                        approximate = True
                rec["lat"], rec["lon"] = new
                rec["city_key"] = name
                rec["snapped_to_building"] = True
                rec["location_precision"] = "settlement_approximate" if approximate else "street_geocode"
                rec["location_source"] = source
                rec.pop("error", None)
                occupied.append(new)
                moved_m = haversine_m(*old, *new)
                stats["moved" if moved_m > 2 else "unchanged"] += 1
                status = "moved" if moved_m > 2 else "unchanged"
            except NoSuitableBuilding:
                stats["unresolved"] += 1
                new = None
                status = "unresolved"
                moved_m = None
            changes.append({"project_number": rec.get("project_number"), "place": name, "status": status,
                            "old": old, "new": new, "source": source if new is not None else None,
                            "moved_m": round(moved_m) if moved_m is not None else None})
        if index % 20 == 0 or index == len(groups):
            log.info("Roof audit %s/%s groups: %s", index, len(groups), {k: v for k, v in stats.items() if k != "groups"})

    report = config.ROOT / ".cache" / "roof-audit.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"stats": stats, "changes": changes}, ensure_ascii=False, indent=2), encoding="utf-8")
    if apply:
        if stats["unresolved"]:
            raise NoSuitableBuilding(f"Refusing to publish an incomplete roof audit: {stats['unresolved']} unresolved")
        save_state(state)
        write_geojson(state)
    log.info("Roof audit %s: %s. Report: %s", "APPLIED" if apply else "DRY RUN", stats, report)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Save corrected state and GeoJSON")
    parser.add_argument("--place", help="Audit one locality first")
    parser.add_argument("--max-groups", type=int, help="Limit for diagnostic run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    audit_roofs(apply=args.apply, place=args.place, max_groups=args.max_groups)


if __name__ == "__main__":
    main()

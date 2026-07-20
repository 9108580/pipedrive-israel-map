"""Sync Pipedrive persons → map GeoJSON."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from . import config
from .geocode import NominatimGeocoder
from .pipedrive_client import PipedriveClient
from .scatter import ResidentialScatter
from .state_store import load_state, save_state, write_geojson

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _occupied_near_city(
    state: dict[str, Any], city_key: str
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for rec in (state.get("persons") or {}).values():
        if rec.get("city_key") == city_key and rec.get("lat") is not None:
            out.append((float(rec["lat"]), float(rec["lon"])))
    return out


def sync(full: bool = False, limit: int | None = None) -> int:
    config.require_token()
    client = PipedriveClient()
    geocoder = NominatimGeocoder()
    scatter = ResidentialScatter()
    state = load_state()
    persons_map: dict[str, Any] = state.setdefault("persons", {})
    next_num = int(state.get("next_project_number") or 1)

    added = 0
    skipped = 0
    failed = 0

    try:
        for person in client.iter_persons_with_address():
            if limit is not None and added >= limit:
                break

            pid = str(person["person_id"])
            address = person["address"]
            existing = persons_map.get(pid) or {}

            # Incremental: skip only when same address already has coordinates.
            # Re-process when missing, no coords, or Pipedrive address changed.
            if not full and pid in persons_map:
                same_addr = (existing.get("address") or "").strip() == address.strip()
                has_coords = existing.get("lat") is not None and existing.get("lon") is not None
                if same_addr and has_coords:
                    skipped += 1
                    continue
            if full and pid in persons_map and persons_map[pid].get("lat") is not None:
                same_addr = (existing.get("address") or "").strip() == address.strip()
                if same_addr:
                    skipped += 1
                    continue

            project_number = int(existing.get("project_number") or next_num)
            if "project_number" not in existing:
                next_num = max(next_num, project_number + 1)

            log.info("Geocoding person %s: %s", pid, address[:80])
            try:
                geo = geocoder.geocode(address)
            except Exception as exc:
                log.exception("Unexpected geocode error for %s: %s", pid, exc)
                failed += 1
                continue

            if not geo:
                failed += 1
                persons_map[pid] = {
                    "person_id": int(pid) if pid.isdigit() else pid,
                    "project_number": project_number,
                    "address": address,
                    "lat": None,
                    "lon": None,
                    "error": "geocode_failed",
                }
                if project_number >= next_num:
                    next_num = project_number + 1
                if (added + failed) % 10 == 0:
                    state["next_project_number"] = next_num
                    save_state(state)
                    write_geojson(state)
                continue

            city_key = (geo.city or address.split(",")[-1]).strip().lower()
            lat, lon = geo.lat, geo.lon
            address_type = "city" if geo.is_city_level else "street"
            occupied = _occupied_near_city(state, city_key)

            if geo.is_city_level:
                lat, lon = scatter.pick_point(geo.lat, geo.lon, occupied, seed=pid)
                log.info(
                    "Scattered city-level person %s near %s -> %.5f,%.5f",
                    pid,
                    city_key,
                    lat,
                    lon,
                )
            else:
                lat, lon = scatter.snap_to_building(
                    geo.lat, geo.lon, occupied, seed=pid
                )

            persons_map[pid] = {
                "person_id": int(pid) if pid.isdigit() else pid,
                "project_number": project_number,
                "address": address,
                "lat": lat,
                "lon": lon,
                "address_type": address_type,
                "city_key": city_key,
                "geocode_display": geo.display_name,
                "snapped_to_building": True,
            }
            if project_number >= next_num:
                next_num = project_number + 1
            added += 1

            if added % 5 == 0:
                state["next_project_number"] = next_num
                save_state(state)
                write_geojson(state)
                log.info(
                    "Progress: added=%s skipped=%s failed=%s", added, skipped, failed
                )
    except Exception:
        log.exception(
            "Sync interrupted after added=%s skipped=%s failed=%s — saving progress",
            added,
            skipped,
            failed,
        )
        state["next_project_number"] = next_num
        save_state(state)
        write_geojson(state)
        raise

    state["next_project_number"] = next_num
    save_state(state)
    out = write_geojson(state)
    log.info(
        "Done. added=%s skipped=%s failed=%s next_number=%s geojson=%s",
        added,
        skipped,
        failed,
        next_num,
        out,
    )
    return added


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sync Pipedrive addresses to Israel map")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="Process all persons missing coordinates (initial scan)",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Only add new persons not yet in state (default)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max new points to add this run (useful for testing)",
    )
    args = parser.parse_args(argv)
    full = bool(args.full)
    sync(full=full, limit=args.limit)


if __name__ == "__main__":
    main()

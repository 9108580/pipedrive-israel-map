"""Sync Pipedrive deals → map GeoJSON (one pin per deal/system)."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from . import config
from .geocode import NominatimGeocoder, is_city_only_address
from .pipedrive_client import PipedriveClient
from .scatter import ResidentialScatter, offset_near
from .state_store import load_state, save_state, write_geojson

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _occupied_near_city(
    records: dict[str, Any], city_key: str
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for rec in records.values():
        if city_key and rec.get("city_key") != city_key:
            continue
        if rec.get("lat") is not None and rec.get("lon") is not None:
            out.append((float(rec["lat"]), float(rec["lon"])))
    return out


def _person_anchor(
    deals_map: dict[str, Any], person_id: int
) -> dict[str, Any] | None:
    """Reuse geocode from another deal of the same person (same address)."""
    for rec in deals_map.values():
        if rec.get("person_id") == person_id and rec.get("lat") is not None:
            return rec
    return None


def migrate_persons_to_deals(
    state: dict[str, Any],
    client: PipedriveClient,
) -> int:
    """Expand legacy person pins into one pin per deal. No new geocoding.

    Sibling deals at the same address get a small offset so markers don't stack.
    Returns number of deal records created.
    """
    if state.get("deals"):
        return 0

    persons = state.get("persons") or {}
    if not persons:
        state["deals"] = {}
        return 0

    log.info("Migrating %s person pins → deals (1 pin per system)…", len(persons))
    persons_index = client.persons_address_index()
    next_num = int(state.get("next_project_number") or 1)
    deals_map: dict[str, Any] = {}
    person_primary: dict[int, str] = {}
    created = 0

    deal_rows = sorted(
        client.iter_deals_with_address(persons_index),
        key=lambda d: int(d["deal_id"]),
    )

    for deal in deal_rows:
        pid = int(deal["person_id"])
        did = str(deal["deal_id"])
        person_rec = persons.get(str(pid)) or {}
        if person_rec.get("lat") is None or person_rec.get("lon") is None:
            # Not on the map yet — leave for incremental/Sunday sync
            continue

        address = deal["address"]
        city_key = person_rec.get("city_key") or ""
        project_number = int(person_rec.get("project_number") or 0)
        occupied = _occupied_near_city(deals_map, city_key)

        if pid not in person_primary:
            lat = float(person_rec["lat"])
            lon = float(person_rec["lon"])
            if project_number <= 0:
                project_number = next_num
                next_num = max(next_num, project_number + 1)
            person_primary[pid] = did
        else:
            base = deals_map[person_primary[pid]]
            lat, lon = offset_near(
                float(base["lat"]),
                float(base["lon"]),
                occupied,
                seed=f"deal-{did}",
            )
            project_number = next_num
            next_num += 1

        deals_map[did] = {
            "deal_id": int(did),
            "person_id": pid,
            "title": deal.get("title") or "",
            "project_number": project_number,
            "address": address,
            "lat": lat,
            "lon": lon,
            "address_type": person_rec.get("address_type", "unknown"),
            "city_key": city_key,
            "geocode_display": person_rec.get("geocode_display"),
            "snapped_to_building": person_rec.get("snapped_to_building", True),
        }
        created += 1

    state["deals"] = deals_map
    state["next_project_number"] = max(
        next_num, int(state.get("next_project_number") or 1)
    )
    log.info(
        "Migration done: %s deal pins (from %s persons with coords)",
        created,
        sum(1 for r in persons.values() if r.get("lat") is not None),
    )
    return created


def sync(full: bool = False, limit: int | None = None, migrate_only: bool = False) -> int:
    config.require_token()
    client = PipedriveClient()
    geocoder = NominatimGeocoder()
    scatter = ResidentialScatter()
    state = load_state()

    migrated = migrate_persons_to_deals(state, client)
    if migrated:
        save_state(state)
        write_geojson(state)

    if migrate_only:
        log.info("migrate-only: skipping geocode sync")
        return migrated

    deals_map: dict[str, Any] = state.setdefault("deals", {})
    next_num = int(state.get("next_project_number") or 1)

    added = 0
    skipped = 0
    failed = 0
    reused = 0

    try:
        persons_index = client.persons_address_index()
        for deal in client.iter_deals_with_address(persons_index):
            if limit is not None and added >= limit:
                break

            did = str(deal["deal_id"])
            address = deal["address"]
            existing = deals_map.get(did) or {}

            if not full and did in deals_map:
                same_addr = (existing.get("address") or "").strip() == address.strip()
                has_coords = (
                    existing.get("lat") is not None and existing.get("lon") is not None
                )
                if same_addr and has_coords:
                    skipped += 1
                    continue
            if full and did in deals_map and deals_map[did].get("lat") is not None:
                same_addr = (existing.get("address") or "").strip() == address.strip()
                if same_addr:
                    skipped += 1
                    continue

            project_number = int(existing.get("project_number") or next_num)
            if "project_number" not in existing:
                next_num = max(next_num, project_number + 1)

            pid = int(deal["person_id"])
            anchor = _person_anchor(deals_map, pid)

            if anchor and (anchor.get("address") or "").strip() == address.strip():
                city_key = anchor.get("city_key") or ""
                occupied = _occupied_near_city(deals_map, city_key)
                lat, lon = offset_near(
                    float(anchor["lat"]),
                    float(anchor["lon"]),
                    occupied,
                    seed=f"deal-{did}",
                )
                deals_map[did] = {
                    "deal_id": int(did),
                    "person_id": pid,
                    "title": deal.get("title") or "",
                    "project_number": project_number,
                    "address": address,
                    "lat": lat,
                    "lon": lon,
                    "address_type": anchor.get("address_type", "unknown"),
                    "city_key": city_key,
                    "geocode_display": anchor.get("geocode_display"),
                    "snapped_to_building": True,
                    "reused_person_geocode": True,
                }
                if project_number >= next_num:
                    next_num = project_number + 1
                added += 1
                reused += 1
                if added % 5 == 0:
                    state["next_project_number"] = next_num
                    save_state(state)
                    write_geojson(state)
                continue

            log.info("Geocoding deal %s (person %s): %s", did, pid, address[:80])
            try:
                geo = geocoder.geocode(address)
            except Exception as exc:
                log.exception("Unexpected geocode error for deal %s: %s", did, exc)
                failed += 1
                continue

            if not geo:
                failed += 1
                deals_map[did] = {
                    "deal_id": int(did),
                    "person_id": pid,
                    "title": deal.get("title") or "",
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
            if not is_city_only_address(address):
                address_type = "street"
            else:
                address_type = "city" if geo.is_city_level else "street"
            occupied = _occupied_near_city(deals_map, city_key)

            if geo.is_city_level:
                lat, lon = scatter.pick_point(geo.lat, geo.lon, occupied, seed=did)
            else:
                lat, lon = scatter.snap_to_building(
                    geo.lat, geo.lon, occupied, seed=did
                )

            deals_map[did] = {
                "deal_id": int(did),
                "person_id": pid,
                "title": deal.get("title") or "",
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
                    "Progress: added=%s reused=%s skipped=%s failed=%s",
                    added,
                    reused,
                    skipped,
                    failed,
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
        "Done. added=%s reused=%s skipped=%s failed=%s next_number=%s geojson=%s",
        added,
        reused,
        skipped,
        failed,
        next_num,
        out,
    )
    return added


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Sync Pipedrive deals to Israel map (one pin per system)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="Process deals missing coordinates",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Only add new/changed deals (default)",
    )
    mode.add_argument(
        "--migrate-only",
        action="store_true",
        help="Expand legacy person pins to deals without geocoding new ones",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max new points to add this run (useful for testing)",
    )
    args = parser.parse_args(argv)
    if args.migrate_only:
        sync(migrate_only=True)
        return
    full = bool(args.full)
    sync(full=full, limit=args.limit)


if __name__ == "__main__":
    main()

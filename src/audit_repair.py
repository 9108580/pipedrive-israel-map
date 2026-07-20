"""Audit Pipedrive addresses vs map state and repair gaps / bad settlement pins."""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

from . import config
from .geocode import (
    NominatimGeocoder,
    extract_settlement,
    haversine_m,
    is_city_only_address,
)
from .pipedrive_client import PipedriveClient
from .scatter import ResidentialScatter
from .state_store import load_state, place_label, save_state, write_geojson

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audit_repair")

DEBUG_LOG = config.ROOT / "debug-cdbd90.log"
SETTLEMENT_DRIFT_M = 2500.0


def _dlog(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    payload = {
        "sessionId": "cdbd90",
        "runId": "full-audit",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with DEBUG_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    # #endregion


def _occupied(
    persons: dict[str, Any], city_key: str, exclude_pid: str
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pid, rec in persons.items():
        if pid == exclude_pid or rec.get("lat") is None:
            continue
        if (rec.get("city_key") or "") == city_key:
            out.append((float(rec["lat"]), float(rec["lon"])))
    return out


def _apply_geo(
    persons: dict[str, Any],
    pid: str,
    address: str,
    project_number: int,
    geo: Any,
    scatter: ResidentialScatter,
) -> None:
    city_key = (geo.city or address.split(",")[-1]).strip().lower()
    occupied = _occupied(persons, city_key, pid)
    if geo.is_city_level:
        lat, lon = scatter.pick_point(geo.lat, geo.lon, occupied, seed=pid)
    else:
        lat, lon = scatter.snap_to_building(geo.lat, geo.lon, occupied, seed=pid)
    persons[pid] = {
        "person_id": int(pid) if pid.isdigit() else pid,
        "project_number": project_number,
        "address": address,
        "lat": lat,
        "lon": lon,
        "address_type": "city" if geo.is_city_level else "street",
        "city_key": city_key,
        "geocode_display": geo.display_name,
        "snapped_to_building": True,
    }


def audit_and_repair(
    *,
    repair: bool = True,
    check_settlement_drift: bool = True,
    limit: int | None = None,
) -> dict[str, int]:
    config.require_token()
    client = PipedriveClient()
    geocoder = NominatimGeocoder()
    scatter = ResidentialScatter()
    state = load_state()
    persons: dict[str, Any] = state.setdefault("persons", {})
    next_num = int(state.get("next_project_number") or 1)

    stats = {
        "pd_with_address": 0,
        "missing": 0,
        "addr_changed": 0,
        "no_coords": 0,
        "drift_fixed": 0,
        "added_or_fixed": 0,
        "failed": 0,
        "ok": 0,
    }

    # Cache settlement geocode results (lat, lon, display, is_city)
    sett_cache: dict[str, Any] = {}

    def settlement_anchor(address: str):
        sett = extract_settlement(address) or address
        key = sett.strip().lower()
        if key in sett_cache:
            return sett_cache[key]
        # Prefer settlement-only query for city-level addresses
        q = sett if is_city_only_address(address) else address
        geo = geocoder.geocode(q if is_city_only_address(address) else sett)
        sett_cache[key] = geo
        return geo

    processed = 0
    for person in client.iter_persons_with_address():
        if limit is not None and processed >= limit:
            break
        processed += 1
        stats["pd_with_address"] += 1
        pid = str(person["person_id"])
        address = (person.get("address") or "").strip()
        existing = persons.get(pid)
        need = False
        reason = ""

        if existing is None:
            need, reason = True, "missing"
            stats["missing"] += 1
        elif existing.get("lat") is None or existing.get("lon") is None:
            need, reason = True, "no_coords"
            stats["no_coords"] += 1
        elif (existing.get("address") or "").strip() != address:
            need, reason = True, "addr_changed"
            stats["addr_changed"] += 1
        elif check_settlement_drift and is_city_only_address(address):
            anchor = settlement_anchor(address)
            if (
                anchor
                and existing.get("lat") is not None
                and haversine_m(
                    float(existing["lat"]),
                    float(existing["lon"]),
                    float(anchor.lat),
                    float(anchor.lon),
                )
                > SETTLEMENT_DRIFT_M
            ):
                need, reason = True, "settlement_drift"
                stats["drift_fixed"] += 1

        if not need:
            stats["ok"] += 1
            continue

        if not repair:
            _dlog(
                "A",
                "audit:need",
                "needs repair (dry-run)",
                {"person_id": pid, "reason": reason, "address": address[:80]},
            )
            continue

        project_number = int((existing or {}).get("project_number") or next_num)
        if existing is None or "project_number" not in (existing or {}):
            next_num = max(next_num, project_number + 1)

        log.info("Repair %s (%s): %s", pid, reason, address[:70])
        try:
            geo = geocoder.geocode(address)
        except Exception as exc:
            log.exception("Geocode error %s: %s", pid, exc)
            stats["failed"] += 1
            continue
        if not geo:
            stats["failed"] += 1
            persons[pid] = {
                "person_id": int(pid) if pid.isdigit() else pid,
                "project_number": project_number,
                "address": address,
                "lat": None,
                "lon": None,
                "error": "geocode_failed",
            }
            _dlog(
                "B",
                "audit:fail",
                "geocode failed",
                {"person_id": pid, "reason": reason, "address": address[:80]},
            )
            continue

        old = {
            "lat": (existing or {}).get("lat"),
            "lon": (existing or {}).get("lon"),
            "display": ((existing or {}).get("geocode_display") or "")[:70],
        }
        _apply_geo(persons, pid, address, project_number, geo, scatter)
        if project_number >= next_num:
            next_num = project_number + 1
        stats["added_or_fixed"] += 1
        _dlog(
            "B",
            "audit:fixed",
            "repaired point",
            {
                "person_id": pid,
                "reason": reason,
                "address": address[:80],
                "old": old,
                "new_lat": persons[pid]["lat"],
                "new_lon": persons[pid]["lon"],
                "new_display": (persons[pid].get("geocode_display") or "")[:80],
                "place": place_label(persons[pid]),
            },
        )

        if stats["added_or_fixed"] % 10 == 0:
            state["next_project_number"] = next_num
            save_state(state)
            write_geojson(state)
            log.info("Progress %s", stats)

    # Orphans in state without coords count as coverage holes in geojson already
    mapped = sum(1 for r in persons.values() if r.get("lat") is not None)
    stats["mapped"] = mapped
    stats["state_total"] = len(persons)

    state["next_project_number"] = next_num
    save_state(state)
    out = write_geojson(state)
    _dlog("C", "audit:done", "audit complete", {**stats, "geojson": str(out)})
    log.info("Audit done %s -> %s", stats, out)
    return stats


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Audit/repair map vs Pipedrive")
    p.add_argument("--dry-run", action="store_true", help="Report only, do not write")
    p.add_argument("--no-drift", action="store_true", help="Skip settlement drift checks")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)
    audit_and_repair(
        repair=not args.dry_run,
        check_settlement_drift=not args.no_drift,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()

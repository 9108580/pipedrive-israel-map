"""One-shot / on-demand quality repair for map pins that would embarrass a client demo."""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from typing import Any

from . import config
from .geocode import (
    NominatimGeocoder,
    extract_settlement,
    haversine_m,
    is_city_only_address,
    normalize_pipedrive_address,
)
from .scatter import ResidentialScatter
from .state_store import load_state, place_label, save_state, write_geojson

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("qa_repair")
DEBUG_LOG = config.ROOT / "debug-cdbd90.log"
_CYR = re.compile(r"[\u0400-\u04FF]")
_BAD_DISP = re.compile(r"^(Israel|ישראל|Израиль)\s*$", re.I)


def _dlog(hid: str, loc: str, msg: str, data: dict) -> None:
    # #region agent log
    with DEBUG_LOG.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "sessionId": "cdbd90",
                    "runId": "qa-repair",
                    "hypothesisId": hid,
                    "location": loc,
                    "message": msg,
                    "data": data,
                    "timestamp": int(time.time() * 1000),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    # #endregion


def _needs_repair(pid: str, rec: dict[str, Any]) -> str | None:
    addr = (rec.get("address") or "").strip()
    disp = (rec.get("geocode_display") or "").strip()
    place = place_label(rec)
    if rec.get("lat") is None or rec.get("lon") is None:
        return "no_coords"
    if _BAD_DISP.match(disp) or disp.lower() in ("israel", "ישראל"):
        return "country_geocode"
    if place in ("ישראל", "Israel", "—", "") or "מועצה" in place:
        return "bad_place_label"
    if _CYR.search(addr):
        return "cyrillic_address"
    # Street-shaped address stored as city with tiny place token
    if not is_city_only_address(addr) and len(place) <= 2:
        return "street_as_tiny_place"
    # City-only: settlement geocode must be nearby (checked later with cache)
    if is_city_only_address(addr):
        return "check_settlement_drift"
    return None


def qa_repair(*, limit: int | None = None) -> dict[str, int]:
    geocoder = NominatimGeocoder()
    scatter = ResidentialScatter()
    state = load_state()
    persons: dict[str, Any] = state.setdefault("persons", {})
    stats = {"scanned": 0, "flagged": 0, "fixed": 0, "failed": 0, "ok": 0}
    sett_cache: dict[str, Any] = {}

    def sett_geo(name: str):
        key = name.strip().lower()
        if key not in sett_cache:
            sett_cache[key] = geocoder.geocode(name)
        return sett_cache[key]

    todo: list[tuple[str, str]] = []
    for pid, rec in persons.items():
        stats["scanned"] += 1
        reason = _needs_repair(pid, rec)
        if reason == "check_settlement_drift":
            addr = rec.get("address") or ""
            sett = extract_settlement(normalize_pipedrive_address(addr)) or normalize_pipedrive_address(addr)
            anchor = sett_geo(sett) if sett else None
            if (
                anchor
                and haversine_m(float(rec["lat"]), float(rec["lon"]), float(anchor.lat), float(anchor.lon))
                > 2500
            ):
                reason = "settlement_drift"
            else:
                reason = None
        if reason:
            todo.append((pid, reason))
            stats["flagged"] += 1
        else:
            stats["ok"] += 1

    _dlog("A", "qa:flagged", "quality flags", {"stats": stats, "sample": todo[:40], "total_todo": len(todo)})

    if limit is not None:
        todo = todo[:limit]

    for pid, reason in todo:
        rec = persons[pid]
        addr = rec.get("address") or ""
        log.info("QA fix %s (%s): %s", pid, reason, addr[:70])
        try:
            geo = geocoder.geocode(addr)
        except Exception as exc:
            log.exception("geocode error %s: %s", pid, exc)
            stats["failed"] += 1
            continue
        if not geo or _BAD_DISP.match((geo.display_name or "").split(",")[0].strip()):
            # Last try: settlement-only
            sett = extract_settlement(normalize_pipedrive_address(addr))
            geo = geocoder.geocode(sett) if sett else None
        if not geo or _BAD_DISP.match((geo.display_name or "").split(",")[0].strip()):
            stats["failed"] += 1
            _dlog("B", "qa:fail", "still bad after geocode", {"person_id": pid, "reason": reason, "address": addr})
            continue

        city_only = is_city_only_address(addr)
        atype = "city" if city_only else "street"
        occupied = [
            (float(p["lat"]), float(p["lon"]))
            for q, p in persons.items()
            if q != pid and p.get("lat") is not None
        ]
        if atype == "city" or geo.is_city_level:
            lat, lon = scatter.pick_point(geo.lat, geo.lon, occupied, seed=pid)
            atype = "city"
        else:
            lat, lon = scatter.snap_to_building(geo.lat, geo.lon, occupied, seed=pid)

        old = {
            "lat": rec.get("lat"),
            "lon": rec.get("lon"),
            "display": (rec.get("geocode_display") or "")[:70],
            "place": place_label(rec),
        }
        rec.update(
            {
                "lat": lat,
                "lon": lon,
                "address": addr,
                "address_type": atype,
                "city_key": (geo.city or "").strip().lower() or rec.get("city_key"),
                "geocode_display": geo.display_name,
                "snapped_to_building": True,
                "error": None,
            }
        )
        stats["fixed"] += 1
        _dlog(
            "B",
            "qa:fixed",
            "repaired pin",
            {
                "person_id": pid,
                "reason": reason,
                "address": addr[:80],
                "old": old,
                "new_lat": lat,
                "new_lon": lon,
                "new_display": (geo.display_name or "")[:80],
                "new_place": place_label(rec),
            },
        )
        if stats["fixed"] % 10 == 0:
            save_state(state)
            write_geojson(state)

    save_state(state)
    out = write_geojson(state)
    # Final gate: no ישראל labels
    from collections import Counter

    import json as _json
    from pathlib import Path

    g = _json.loads(Path(out).read_text(encoding="utf-8"))
    places = Counter(f["properties"].get("place") for f in g["features"])
    israel_left = places.get("ישראל", 0) + places.get("Israel", 0)
    _dlog(
        "C",
        "qa:done",
        "qa repair complete",
        {"stats": stats, "israel_labels_left": israel_left, "geojson": str(out), "features": len(g["features"])},
    )
    log.info("QA done %s israel_labels=%s -> %s", stats, israel_left, out)
    if israel_left:
        raise SystemExit(f"Still have {israel_left} Israel country labels on map")
    return stats


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)
    # geocode does not need Pipedrive token
    qa_repair(limit=args.limit)


if __name__ == "__main__":
    main()
"""Persistent state + GeoJSON helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import config


def ensure_dirs() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.MAP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_state(path: Path | None = None) -> dict[str, Any]:
    ensure_dirs()
    p = path or config.STATE_PATH
    if not p.exists():
        return {
            "next_project_number": 1,
            "deals": {},  # deal_id -> project record (one pin per deal/system)
            "persons": {},  # legacy; kept for migration / repair fallback
        }
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    ensure_dirs()
    p = path or config.STATE_PATH
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


def place_label(rec: dict[str, Any]) -> str:
    """Only a canonical identity may produce a locality label."""
    from .localities import registry
    locality = registry()[0].get(str(rec.get("locality_id") or ""))
    return locality["name"] if locality else ""


def map_records(state: dict[str, Any]) -> dict[str, Any]:
    """Active map pins: prefer deals (1 pin per system), else legacy persons."""
    deals = state.get("deals")
    if isinstance(deals, dict) and deals:
        return deals
    return state.setdefault("persons", {})


def state_to_geojson(state: dict[str, Any]) -> dict[str, Any]:
    features = []
    records = map_records(state)
    # Stable order by project number
    items = sorted(
        records.values(),
        key=lambda x: int(x.get("project_number") or 0),
    )
    for rec in items:
        if rec.get("lat") is None or rec.get("lon") is None:
            continue
        num = int(rec["project_number"])
        place = place_label(rec)
        props: dict[str, Any] = {
            "name": f"Project {num:04d}",
            "project_number": num,
            "address_type": rec.get("address_type", "unknown"),
            "person_id": rec.get("person_id"),
            "place": place,
            "locality_id": rec.get("locality_id"),
            "locality_status": "resolved" if rec.get("locality_id") else "unresolved",
            "approximate": rec.get("location_precision") == "settlement_approximate" or rec.get("address_type") == "city",
        }
        if rec.get("deal_id") is not None:
            props["deal_id"] = rec.get("deal_id")
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [rec["lon"], rec["lat"]],
                },
                "properties": props,
            }
        )
    return {"type": "FeatureCollection", "features": features, "crm_summary": state.get("crm_summary", {})}


def write_geojson(state: dict[str, Any], path: Path | None = None) -> Path:
    ensure_dirs()
    p = path or config.GEOJSON_PATH
    geo = state_to_geojson(state)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    return p

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
            "persons": {},  # person_id -> project record
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
    """Human-readable settlement / village name for map labels."""
    disp = (rec.get("geocode_display") or "").strip()
    city_key = (rec.get("city_key") or "").strip()
    parts = [p.strip() for p in disp.split(",") if p.strip()]

    skip_sub = ("מועצה", "נפת", "מחוז", "שטח", "ישראל", "israel", "الأراضي")

    def usable(part: str) -> bool:
        low = part.lower()
        if part.isdigit() or len(part) < 2:
            return False
        return not any(s in low for s in skip_sub)

    # City-level geocodes: first display segment is usually the settlement
    if rec.get("address_type") == "city" and parts and usable(parts[0]):
        return parts[0]

    # Prefer a display segment that matches city_key (when city_key is not a council)
    if city_key and "מועצה" not in city_key:
        for p in parts:
            if city_key in p.lower() or p.lower() in city_key:
                if usable(p):
                    return p

    for p in parts:
        if usable(p):
            return p

    if city_key and "מועצה" not in city_key:
        return city_key

    addr = (rec.get("address") or "").strip()
    if addr:
        head = addr.split(",")[0].strip()
        # Drop trailing house numbers for label
        head = re.sub(r"\s+\d+[א-תA-Za-z]?\s*$", "", head).strip()
        if head:
            return head
    return city_key or "ישראל"


def state_to_geojson(state: dict[str, Any]) -> dict[str, Any]:
    features = []
    persons = state.get("persons") or {}
    # Stable order by project number
    items = sorted(
        persons.values(),
        key=lambda x: int(x.get("project_number") or 0),
    )
    for rec in items:
        if rec.get("lat") is None or rec.get("lon") is None:
            continue
        num = int(rec["project_number"])
        place = place_label(rec)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [rec["lon"], rec["lat"]],
                },
                "properties": {
                    "name": f"Project {num:04d}",
                    "project_number": num,
                    "address_type": rec.get("address_type", "unknown"),
                    "person_id": rec.get("person_id"),
                    "place": place,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def write_geojson(state: dict[str, Any], path: Path | None = None) -> Path:
    ensure_dirs()
    p = path or config.GEOJSON_PATH
    geo = state_to_geojson(state)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
    return p

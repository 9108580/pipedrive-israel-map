"""Persistent state + GeoJSON helpers."""

from __future__ import annotations

import json
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

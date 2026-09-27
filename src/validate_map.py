"""Block publication when any recorded system is missing from the roof map."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def validate() -> dict[str, int]:
    state = json.loads((ROOT / "data" / "state.json").read_text(encoding="utf-8"))
    records = state.get("deals") or state.get("persons") or {}
    geojson = json.loads((ROOT / "map" / "data" / "projects.geojson").read_text(encoding="utf-8"))
    features = geojson.get("features", [])
    by_number = {int(r["project_number"]): r for r in records.values()}
    if len(by_number) != len(records):
        raise ValueError("Duplicate project numbers in state")
    visible = {int(f["properties"]["project_number"]): f for f in features}
    if len(visible) != len(features) or set(visible) != set(by_number):
        raise ValueError(f"Map/state mismatch: {len(features)} features for {len(records)} systems")
    for number, rec in by_number.items():
        if rec.get("lat") is None or rec.get("lon") is None or not rec.get("snapped_to_building"):
            raise ValueError(f"System {number} has no verified roof coordinate")
        coords = visible[number]["geometry"]["coordinates"]
        if coords != [rec["lon"], rec["lat"]]:
            raise ValueError(f"System {number} has different state/map coordinates")
    return {"systems": len(records), "visible": len(features)}


if __name__ == "__main__":
    print(json.dumps(validate()))

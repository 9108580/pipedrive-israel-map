"""Independently check proposed pins against roof and exclusion indexes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.geocode import is_city_only_address  # noqa: E402
from src.scatter import _near_polygon  # noqa: E402
from src.state_store import load_state, map_records  # noqa: E402


def verify() -> dict:
    records = map_records(load_state())
    by_number = {rec["project_number"]: (key, rec) for key, rec in records.items()}
    audit = json.loads((ROOT / ".cache" / "roof-audit.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "data" / "roof_candidates.json").read_text(encoding="utf-8"))["places"]
    exclusions = json.loads((ROOT / ".cache" / "excluded_grounds.json").read_text(encoding="utf-8"))
    street_matches = json.loads((ROOT / ".cache" / "street_roof_matches.json").read_text(encoding="utf-8"))
    result = {"checked": 0, "catalog_roofs": 0, "street_matches": 0,
              "outside_roofs": [], "unverified_street": [],
              "on_excluded_ground": [], "hurfeish": 0}
    for change in audit["changes"]:
        point = change["new"]
        if point is None:
            continue
        result["checked"] += 1
        key, rec = by_number[change["project_number"]]
        place = change["place"]
        approximate = rec.get("address_type") == "city" or is_city_only_address(rec.get("address") or "")
        if approximate:
            if point in catalog[place]["roofs"]:
                result["catalog_roofs"] += 1
            else:
                result["outside_roofs"].append(change["project_number"])
        elif street_matches.get(key, {}).get("roof") == point:
            result["street_matches"] += 1
        else:
            result["unverified_street"].append(change["project_number"])
        if any(_near_polygon(*point, polygon, 20) for polygon in exclusions.get(place, ())):
            result["on_excluded_ground"].append(change["project_number"])
        if place == "حرفيش":
            result["hurfeish"] += 1
    return result


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=True))
    if result["outside_roofs"] or result["unverified_street"] or result["on_excluded_ground"]:
        raise SystemExit(1)

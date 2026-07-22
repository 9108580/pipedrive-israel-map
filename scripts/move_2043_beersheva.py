"""Move person 2043 (קרקע / bogus address 54347240) to Be'er Sheva."""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.state_store import load_state, place_label, save_state, write_geojson

PID = "2043"
# Nominatim city hit for באר-שבע
LAT = 31.2508829
LON = 34.7912803
DISPLAY = "באר שבע, נפת באר שבע, מחוז הדרום, ישראל"


def main() -> None:
    state = load_state()
    persons = state.get("persons") or {}
    rec = persons.get(PID)
    if not rec:
        raise SystemExit(f"person {PID} not in state")

    print("before:", rec.get("address"), rec.get("lat"), rec.get("lon"), place_label(rec))

    rec["address"] = "באר שבע"
    rec["lat"] = LAT
    rec["lon"] = LON
    rec["address_type"] = "city"
    rec["city_key"] = "באר שבע"
    rec["geocode_display"] = DISPLAY
    rec["snapped_to_building"] = False

    save_state(state)
    out = write_geojson(state)
    print("after:", place_label(rec), LAT, LON)
    print("GeoJSON:", out)


if __name__ == "__main__":
    main()

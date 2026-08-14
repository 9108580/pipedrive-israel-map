"""Move Lior Ne'eman (deal 1750) onto HaHayil in moshav Neta'im."""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.geocode import haversine_m
from src.scatter import ResidentialScatter
from src.state_store import load_state, place_label, save_state, write_geojson

DEAL_ID = "1750"
PERSON_ID = "1693"
# OSM residential way החי"ל inside נטעים
STREET_LAT = 31.9450477
STREET_LON = 34.7747238
DISPLAY = "נטעים, נפת רחובות, מחוז המרכז, ישראל"
CITY_KEY = "נטעים"


def _apply(rec: dict, lat: float, lon: float) -> None:
    rec["lat"] = lat
    rec["lon"] = lon
    rec["address_type"] = "street"
    rec["city_key"] = CITY_KEY
    rec["geocode_display"] = DISPLAY
    rec["snapped_to_building"] = True
    rec["geocode_query"] = "החי\"ל, נטעים"
    rec.pop("error", None)


def main() -> None:
    state = load_state()
    deals = state.setdefault("deals", {})
    persons = state.setdefault("persons", {})
    rec = deals.get(DEAL_ID)
    if not rec:
        raise SystemExit(f"deal {DEAL_ID} not in state")

    old_lat, old_lon = rec.get("lat"), rec.get("lon")
    print(
        "before:",
        rec.get("address"),
        old_lat,
        old_lon,
        place_label(rec),
        rec.get("city_key"),
    )
    if old_lat is not None and old_lon is not None:
        print(
            f"drift_m={haversine_m(float(old_lat), float(old_lon), STREET_LAT, STREET_LON):.0f}"
        )

    occupied = [
        (float(r["lat"]), float(r["lon"]))
        for did, r in deals.items()
        if did != DEAL_ID and r.get("lat") is not None
    ]
    scatter = ResidentialScatter()
    lat, lon = scatter.snap_to_building(
        STREET_LAT, STREET_LON, occupied, seed=DEAL_ID, max_snap_m=180.0
    )

    _apply(rec, lat, lon)
    person = persons.get(PERSON_ID)
    if person:
        _apply(person, lat, lon)
        person["address"] = rec.get("address")

    save_state(state)
    out = write_geojson(state)
    print("after:", place_label(rec), lat, lon)
    print("GeoJSON:", out)


if __name__ == "__main__":
    main()

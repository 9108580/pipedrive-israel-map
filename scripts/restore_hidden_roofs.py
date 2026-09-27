"""Restore the 14 pins hidden by the 2026 roof audit onto screened buildings.

The selected points are in roof_overrides.json or the existing roof catalog.
They describe approximate map locations, not confirmed customer premises.
"""
from __future__ import annotations

from src.roof_catalog import load_catalog
from src.state_store import load_state, map_records, save_state, write_geojson


# deal ID: (Pipedrive locality, roof latitude, roof longitude)
RECOVERED = {
    "56": ("ראשון לציון", 31.9714164, 34.773952),
    "1569": ("כחול", 32.8879273, 35.2915668),
    "1577": ("כחול", 32.8883325, 35.2917084),
    "1678": ("ג'וליס", 32.9443771, 35.1857761),
    "1904": ("חיפה", 32.8024989, 35.0080054),
    "1918": ("גנות הדר", 32.3197753, 34.9000868),
    "1932": ("ביתר עילית", 31.6944992, 35.1046762),
    "1985": ("מעלות תרשיחא", 33.0231372, 35.2773053),
    "1987": ("מרר", 32.8868389, 35.4070536),
    "2220": ("כברי", 33.020631, 35.1485576),
    "2491": ("כברי", 33.0223374, 35.1480217),
    "2530": ("כסרא", 32.9646913, 35.303543),
    "2712": ("מעלות תרשיחא", 33.0172403, 35.2802158),
    "2713": ("מעלות תרשיחא", 33.0209749, 35.2833873),
}


def restore() -> int:
    state = load_state()
    records = map_records(state)
    catalog = load_catalog()["places"]
    missing = {key for key, rec in records.items() if rec.get("lat") is None or rec.get("lon") is None}
    if missing != set(RECOVERED):
        raise ValueError(f"Unexpected missing deals: {sorted(missing ^ set(RECOVERED))}")
    for key, (place, lat, lon) in RECOVERED.items():
        entry = catalog[place]
        if [lat, lon] not in entry["roofs"]:
            raise ValueError(f"Deal {key} has no catalogued roof in {place}")
        rec = records[key]
        rec.update({
            "lat": lat,
            "lon": lon,
            "city_key": place,
            "snapped_to_building": True,
            "location_precision": "settlement_approximate",
            "location_source": entry["source"],
        })
        rec.pop("error", None)
    save_state(state)
    write_geojson(state)
    return len(RECOVERED)


if __name__ == "__main__":
    print(f"Restored {restore()} roof pins")

"""Fix mis-placed Tekuma pins and relabel Halamish→Arad; rebuild GeoJSON."""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.geocode import apply_alias
from src.state_store import load_state, place_label, save_state, write_geojson

# Correct moshav Tekuma (Sdot Negev) — Nominatim village hit
TKUMA_LAT = 31.4485605
TKUMA_LON = 34.5775724
TKUMA_DISPLAY = "תקומה, מועצה אזורית שדות נגב, נפת באר שבע, מחוז הדרום, ישראל"


def main() -> None:
    state = load_state()
    persons = state.get("persons") or {}
    fixed_tkuma = 0
    fixed_halamish = 0

    for pid, rec in persons.items():
        addr = (rec.get("address") or "").strip()
        disp = (rec.get("geocode_display") or "").strip()
        city = (rec.get("city_key") or "").strip()
        low_addr = addr.lower()

        # --- Tekuma: force correct village coords ---
        if (
            "tkuma" in low_addr
            or "tequma" in low_addr
            or city == "תקומה"
            or disp.startswith("תקומה,")
        ):
            old = (rec.get("lat"), rec.get("lon"))
            rec["lat"] = TKUMA_LAT
            rec["lon"] = TKUMA_LON
            rec["geocode_display"] = TKUMA_DISPLAY
            rec["city_key"] = "תקומה"
            rec["address_type"] = "city"
            # Scatter slightly if multiple pins would stack
            fixed_tkuma += 1
            print(f"Tekuma {pid}: {old} -> ({TKUMA_LAT}, {TKUMA_LON}) | {addr}")

        # --- Halamish (Arad neighborhood): keep coords, fix label via city ---
        if (
            "halamish" in low_addr
            or "חלמיש" in addr
            or "חלמיש" in disp
            or city == "חלמיש"
        ):
            # Only when geocoder already tied it to Arad (or address says Halamish in Arad area)
            if "ערד" in disp or "arad" in low_addr or "halamish" in low_addr:
                rec["city_key"] = "ערד"
                if "ערד" not in disp:
                    rec["geocode_display"] = "ערד, נפת באר שבע, מחוז הדרום, ישראל"
                fixed_halamish += 1
                print(
                    f"Halamish→Arad {pid}: place will be {place_label(rec)!r} | {addr}"
                )

    # Light scatter for multiple Tekuma city-only pins so they don't overlap
    tkuma_pids = [
        pid
        for pid, rec in persons.items()
        if (rec.get("city_key") or "") == "תקומה" and rec.get("lat") is not None
    ]
    if len(tkuma_pids) > 1:
        # small offsets ~40–80 m east/north
        offsets = [(0.0, 0.0), (0.00035, 0.00025), (-0.0003, 0.0004)]
        for i, pid in enumerate(sorted(tkuma_pids, key=lambda x: int(x))):
            dx, dy = offsets[i % len(offsets)]
            persons[pid]["lat"] = TKUMA_LAT + dy
            persons[pid]["lon"] = TKUMA_LON + dx
            print(
                f"  scatter {pid}: ({persons[pid]['lat']:.6f}, {persons[pid]['lon']:.6f})"
            )

    save_state(state)
    out = write_geojson(state)
    print(f"Fixed Tekuma: {fixed_tkuma}, Halamish→Arad: {fixed_halamish}")
    print(f"GeoJSON: {out}")

    # Verify
    for place in ("תקומה", "זימרת", "ערד", "חלמיש"):
        n = sum(
            1
            for rec in persons.values()
            if rec.get("lat") is not None and place_label(rec) == place
        )
        print(f"  place {place!r}: {n} markers")


if __name__ == "__main__":
    main()

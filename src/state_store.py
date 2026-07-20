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
    """Human-readable settlement / village name for map labels (prefer Hebrew)."""
    disp = (rec.get("geocode_display") or "").strip()
    city_key = (rec.get("city_key") or "").strip()
    parts = [p.strip() for p in disp.split(",") if p.strip()]
    addr = (rec.get("address") or "").strip()

    skip_sub = ("מועצה", "נפת", "מחוז", "שטח", "ישראל", "israel", "الأراضي")
    he_re = re.compile(r"[\u0590-\u05FF]")
    ar_re = re.compile(r"[\u0600-\u06FF]")

    def usable(part: str) -> bool:
        low = part.lower()
        if part.isdigit() or len(part) < 2:
            return False
        return not any(s in low for s in skip_sub)

    def score(part: str) -> int:
        """Higher = better label; prefer Hebrew over Arabic/Latin."""
        if not usable(part):
            return -1
        s = 0
        if he_re.search(part):
            s += 10
        if ar_re.search(part):
            s -= 5
        if re.search(r"[A-Za-z]", part) and not he_re.search(part):
            s -= 1
        return s

    candidates: list[str] = []
    if rec.get("address_type") == "city" and parts:
        candidates.append(parts[0])
    if city_key and "מועצה" not in city_key:
        candidates.append(city_key)
        for p in parts:
            if city_key in p.lower() or p.lower() in city_key:
                candidates.append(p)
    candidates.extend(parts)
    if addr:
        head = addr.split(",")[0].strip()
        head = re.sub(r"\s+\d+[א-תA-Za-z]?\s*$", "", head).strip()
        if head:
            candidates.append(head)

    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    best = ""
    best_score = -1
    for c in ordered:
        sc = score(c)
        if sc > best_score:
            best_score = sc
            best = c

    if not best:
        best = city_key or "ישראל"

    # Never label with regional council alone — fall back to address settlement
    if "מועצה" in (best or "") or best_score < 0:
        if addr:
            head = addr.split(",")[0].strip()
            head = re.sub(r"\s+\d+[א-תA-Za-z]?\s*$", "", head).strip()
            parts_addr = [p.strip() for p in addr.split(",") if p.strip()]
            if parts_addr and re.fullmatch(r"\d+[א-תA-Za-z]?", parts_addr[0]) and len(parts_addr) > 1:
                head = re.sub(r"\s+\d+[א-תA-Za-z]?\s*$", "", parts_addr[1]).strip() or head
            if head and "מועצה" not in head:
                best = head
        for p in parts:
            if usable(p) and he_re.search(p):
                best = p
                break

    # Arabic / Latin → Hebrew via offline cache from Nominatim accept-language=he
    cache_path = config.DATA_DIR / "place_he.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            mapped = cache.get(best)
            if mapped and he_re.search(mapped):
                return mapped
            # also try normalized keys
            mapped = cache.get(best.strip())
            if mapped and he_re.search(mapped):
                return mapped
        except Exception:
            pass

    return best


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

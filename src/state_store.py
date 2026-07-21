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
    """Human-readable settlement / village name for map labels (prefer Hebrew).

    Never returns country-only labels like ישראל / Israel.
    For street addresses prefers the city/town token from geocode display.
    """
    disp = (rec.get("geocode_display") or "").strip()
    city_key = (rec.get("city_key") or "").strip()
    parts = [p.strip() for p in disp.split(",") if p.strip()]
    addr = (rec.get("address") or "").strip()
    atype = rec.get("address_type") or "city"

    skip_sub = (
        "מועצה",
        "נפת",
        "מחוז",
        "שטח",
        "ישראל",
        "israel",
        "израиль",
        "الأراضي",
        "palestine",
    )
    he_re = re.compile(r"[\u0590-\u05FF]")
    ar_re = re.compile(r"[\u0600-\u06FF]")
    banned = {"ישראל", "israel", "израиль", "palestine"}

    def usable(part: str) -> bool:
        low = part.lower().strip()
        if not part or part.isdigit() or len(part) < 2:
            return False
        if low in banned:
            return False
        return not any(s in low for s in skip_sub)

    def score(part: str) -> int:
        if not usable(part):
            return -1
        s = 0
        if he_re.search(part):
            s += 10
        if ar_re.search(part):
            s -= 5
        if re.search(r"[A-Za-z]", part) and not he_re.search(part):
            s -= 1
        # Prefer real locality names over 1–2 letter street stubs
        if len(part) <= 2:
            s -= 4
        return s

    def from_address() -> str:
        if not addr:
            return ""
        # Cyrillic / alias-normalized settlement (Басмат-Табун → בסמת טבעון)
        try:
            from .geocode import extract_settlement, normalize_pipedrive_address

            norm = normalize_pipedrive_address(addr)
            if norm:
                sett = extract_settlement(norm) or norm.split(",")[0].strip()
                if sett and sett.lower() not in banned and not re.search(r"[\u0400-\u04FF]", sett):
                    return sett
        except Exception:
            pass
        parts_addr = [p.strip() for p in addr.split(",") if p.strip()]
        head = parts_addr[0] if parts_addr else addr
        head = re.sub(r"\s+\d+\s*[א-תA-Za-z]?\s*$", "", head).strip()
        if parts_addr and re.fullmatch(r"\d+[א-תA-Za-z]?", parts_addr[0]) and len(parts_addr) > 1:
            head = re.sub(r"\s+\d+\s*[א-תA-Za-z]?\s*$", "", parts_addr[1]).strip()
        if head.lower() in banned or "израиль" in head.lower():
            return ""
        # Prefer not publishing raw Cyrillic on the public map
        if re.search(r"[\u0400-\u04FF]", head):
            return ""
        return head

    candidates: list[str] = []
    # Street pins: prefer city (later parts) over street name (first part)
    if atype == "street" and parts:
        for p in reversed(parts):
            if usable(p):
                candidates.append(p)
    if atype == "city" and parts:
        candidates.append(parts[0])
    if city_key and "מועצה" not in city_key and city_key.lower() not in banned:
        candidates.append(city_key)
        for p in parts:
            if city_key in p.lower() or p.lower() in city_key:
                candidates.append(p)
    candidates.extend(parts)
    addr_head = from_address()
    if addr_head:
        candidates.append(addr_head)

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

    if not best or best.lower() in banned or "מועצה" in best or best_score < 0:
        if addr_head and addr_head.lower() not in banned:
            best = addr_head
        else:
            for p in parts:
                if usable(p) and he_re.search(p) and len(p) >= 3:
                    best = p
                    break

    if not best or best.lower() in banned:
        best = addr_head or (parts[0] if parts and usable(parts[0]) else "")

    # Arabic / Latin → Hebrew via offline cache
    cache_path = config.DATA_DIR / "place_he.json"
    if best and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            mapped = cache.get(best) or cache.get(best.strip())
            if mapped and he_re.search(mapped) and mapped.lower() not in banned:
                return mapped
        except Exception:
            pass

    # Absolute last resort — never publish "ישראל" as a village label
    if not best or best.lower() in banned:
        return addr_head or "—"

    # Neighborhood / spelling aliases (e.g. חלמיש→ערד, Tkuma→תקומה)
    try:
        from .geocode import apply_alias

        aliased = apply_alias(best)
        if aliased and aliased != best:
            best = aliased
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

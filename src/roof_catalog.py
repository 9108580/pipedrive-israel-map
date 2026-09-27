"""Deterministic roof selection from the checked Microsoft footprint catalog."""
from __future__ import annotations

import json
import random
import re
import unicodedata
from functools import lru_cache

from . import config
from .geocode import apply_alias, extract_settlement, haversine_m, is_city_only_address
from .scatter import NoSuitableBuilding


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    path = config.DATA_DIR / "roof_candidates.json"
    if not path.exists():
        catalog = {"places": {}}
    else:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    override_path = config.DATA_DIR / "roof_overrides.json"
    if override_path.exists():
        overrides = json.loads(override_path.read_text(encoding="utf-8"))
        for name, override in overrides.get("places", {}).items():
            base = catalog["places"].get(name, {})
            roofs = list(dict.fromkeys(
                tuple(p) for p in [*(base.get("roofs") or []), *(override.get("roofs") or [])]
            ))
            catalog["places"][name] = {**base, **override, "roofs": [list(p) for p in roofs]}
    return catalog


def _name_key(value: str) -> str:
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value).casefold())


def resolve_place_key(
    address: str,
    fallback: str,
    display: str = "",
    known_places: dict | None = None,
) -> str:
    """Prefer an explicit CRM locality over a geocoder's street/region label.

    A bare name is trusted only when it agrees with the geocoder's first label;
    otherwise a street such as "משואה 314" could be another locality's name.
    """
    places = known_places if known_places is not None else load_catalog().get("places", {})
    by_name = {_name_key(name): name for name in places}
    stripped = re.sub(r",?\s*(?:Israel|ישראל)\s*$", "", address.strip(), flags=re.I).strip(" ,")
    parts = [part.strip() for part in stripped.split(",") if part.strip()]
    candidate = extract_settlement(address) if parts else ""
    explicit = len(parts) >= 2
    if candidate and (explicit or is_city_only_address(address)):
        canonical = apply_alias(candidate)
        matched = by_name.get(_name_key(canonical))
        first_label = display.split(",")[0].strip()
        # These CRM locality names are unambiguous; Nominatim has resolved
        # them to a distant memorial, nature reserve, or road junction.
        trusted_bare = _name_key(candidate) in {_name_key(x) for x in ("גוליס", "מרר", "כסרא")}
        if matched and (explicit or _name_key(candidate) == _name_key(first_label) or trusted_bare):
            return matched
    return by_name.get(_name_key(fallback), fallback.strip().casefold())


def pick_roof(place_key: str, occupied: list[tuple[float, float]], seed: str | int) -> tuple[float, float]:
    entry = load_catalog().get("places", {}).get(place_key.strip().casefold()) or {}
    roofs = entry.get("roofs") or []
    if not roofs:
        raise NoSuitableBuilding(f"No verified footprint catalog for {place_key!r}")
    order = list(range(len(roofs)))
    random.Random(str(seed)).shuffle(order)
    for i in order:
        point = tuple(roofs[i])
        if all(haversine_m(*point, *other) > 12 for other in occupied):
            return point
    return tuple(roofs[order[0]])


def pick_nearest_roof(
    place_key: str,
    target: tuple[float, float],
    occupied: list[tuple[float, float]],
) -> tuple[float, float]:
    """Use the nearest screened roof when an exact street roof is unavailable."""
    entry = load_catalog().get("places", {}).get(place_key.strip().casefold()) or {}
    roofs = entry.get("roofs") or []
    if not roofs:
        raise NoSuitableBuilding(f"No verified footprint catalog for {place_key!r}")
    ordered = sorted((tuple(p) for p in roofs), key=lambda p: haversine_m(*p, *target))
    return next((p for p in ordered if all(haversine_m(*p, *other) > 12 for other in occupied)), ordered[0])

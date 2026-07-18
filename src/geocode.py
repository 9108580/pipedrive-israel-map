"""Geocoding via Nominatim + address classification."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

# Hebrew / Latin street-ish patterns with house numbers
_STREET_NUM = re.compile(
    r"(?:"
    r"\d{1,4}[א-תA-Za-z]?"  # number then optional letter
    r"|"
    r"[א-תA-Za-z][\u0590-\u05FFa-zA-Z\s\-']{1,40}\s+\d{1,4}"  # street then number
    r")"
)
_ONLY_CITYISH = re.compile(
    r"^[\u0590-\u05FFa-zA-Z\s\-'\"]+(?:,\s*(?:Israel|ישראל))?$",
    re.IGNORECASE,
)


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    display_name: str
    is_city_level: bool
    city: str


class NominatimGeocoder:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "application/json",
            }
        )
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = config.NOMINATIM_MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def geocode(self, address: str) -> GeocodeResult | None:
        query = address.strip()
        if not query:
            return None
        if "israel" not in query.lower() and "ישראל" not in query:
            query = f"{query}, Israel"

        self._throttle()
        params = {
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": config.COUNTRY_CODES,
            "viewbox": config.ISRAEL_VIEWBOX,
            "bounded": 0,
            "addressdetails": 1,
        }
        try:
            r = self.session.get(
                f"{config.NOMINATIM_URL}/search",
                params=params,
                timeout=60,
            )
            r.raise_for_status()
            results = r.json()
        except Exception as exc:
            log.warning("Nominatim failed for %r: %s", address, exc)
            return None

        if not results:
            log.warning("No geocode for %r", address)
            return None

        hit = results[0]
        addr = hit.get("address") or {}
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or addr.get("suburb")
            or ""
        )
        return GeocodeResult(
            lat=float(hit["lat"]),
            lon=float(hit["lon"]),
            display_name=hit.get("display_name") or query,
            is_city_level=is_city_only_address(address),
            city=city,
        )


def is_city_only_address(address: str) -> bool:
    """Heuristic: True when address looks like settlement name only."""
    text = address.strip()
    # Drop trailing country
    text = re.sub(r",?\s*(Israel|ישראל)\s*$", "", text, flags=re.IGNORECASE).strip()
    text = text.strip(" ,")
    if not text:
        return True
    # Has digits that look like house numbers → full address
    if re.search(r"\d", text):
        # Could be postal code only — still treat as more specific if comma parts > 1 with digit
        return False
    # Single token / few words without street number → city-level
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) <= 1:
        return True
    # Multiple parts but no digits → likely city, region
    return True


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))

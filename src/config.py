"""Configuration loaded from environment / .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PIPEDRIVE_API_TOKEN = os.getenv("PIPEDRIVE_API_TOKEN", "").strip()
PIPEDRIVE_COMPANY_DOMAIN = os.getenv("PIPEDRIVE_COMPANY_DOMAIN", "mescoil").strip()

NOMINATIM_URL = os.getenv(
    "NOMINATIM_URL", "https://nominatim.openstreetmap.org"
).rstrip("/")
OVERPASS_URL = os.getenv(
    "OVERPASS_URL", "https://overpass-api.de/api/interpreter"
).strip()
USER_AGENT = os.getenv(
    "USER_AGENT", "pipedrive-israel-map/1.0 (github-actions)"
)

DATA_DIR = ROOT / "data"
MAP_DATA_DIR = ROOT / "map" / "data"
STATE_PATH = DATA_DIR / "state.json"
GEOJSON_PATH = MAP_DATA_DIR / "projects.geojson"

# Israel-ish bounding box for geocode bias
ISRAEL_VIEWBOX = "34.2,29.4,35.9,33.5"  # left,bottom,right,top
COUNTRY_CODES = "il"

# Nominatim: max 1 request/sec for public instance
NOMINATIM_MIN_INTERVAL = float(os.getenv("NOMINATIM_MIN_INTERVAL", "1.1"))
OVERPASS_MIN_INTERVAL = float(os.getenv("OVERPASS_MIN_INTERVAL", "2.0"))

# Min distance (meters) between scattered points in same locality
MIN_POINT_DISTANCE_M = float(os.getenv("MIN_POINT_DISTANCE_M", "80"))


def require_token() -> str:
    if not PIPEDRIVE_API_TOKEN:
        raise SystemExit(
            "PIPEDRIVE_API_TOKEN is missing. Set it in .env or GitHub Secrets."
        )
    return PIPEDRIVE_API_TOKEN

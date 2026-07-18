"""Geocoding via Nominatim + address classification."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

# Broader than IL-only: includes West Bank / Golan tagged as Palestine in OSM
REGION_BBOX = (29.3, 34.1, 33.5, 35.95)  # south, west, north, east

# English / noisy Pipedrive spellings → Hebrew (or better Latin) settlement names
_SETTLEMENT_ALIASES_RAW: dict[str, str] = {
    "netiv hagdud": "נתיב הגדוד",
    "fatsa'el": "פצאל",
    "fatsael": "פצאל",
    "petzael": "פצאל",
    "petza'el": "פצאל",
    "mehora": "מכורה",
    "ashdot ya'akov meuhad": "אשדות יעקב מאוחד",
    "ashdot yaakov meuhad": "אשדות יעקב מאוחד",
    "ashdot ya'akov ihud": "אשדות יעקב איחוד",
    "ashdot yaakov ihud": "אשדות יעקב איחוד",
    "karnei shomron": "קרני שומרון",
    "ma'ale efrayim": "מעלה אפרים",
    "maale efrayim": "מעלה אפרים",
    "tverya": "טבריה",
    "kakhol": "כחול",
    "kokhav ya'ir tzur yigal": "כוכב יאיר צור יגאל",
    "kokhav yair tzur yigal": "כוכב יאיר צור יגאל",
    "alfei menashe": "אלפי מנשה",
    "elkana": "אלקנה",
    "nokdim": "נוקדים",
    "giv'at ze'ev": "גבעת זאב",
    "givat zeev": "גבעת זאב",
    "beit aryeh-ofarim": "בית אריה עופרים",
    "beit aryeh ofarim": "בית אריה עופרים",
    "ateret": "עטרת",
    "kfar rosh hanikra": "כפר ראש הנקרה",
    "rosh hanikra": "ראש הנקרה",
    "tsurit": "צורית",
    "shalomi": "שלומי",
    "shlomi": "שלומי",
    "julis": "ג'וליס",
    "qatsrin": "קצרין",
    "katsrin": "קצרין",
    "har adar": "הר אדר",
    "halamish": "חלמיש",
    "kfar ha-oranim": "כפר האורנים",
    "kdumim": "קדומים",
    "ets efraim": "עץ אפרים",
    "etsefraim": "עץ אפרים",
    "bruchim qela' alon": "קלע אלון",
    "bruchim qela alon": "קלע אלון",
    "qela alon": "קלע אלון",
    "sajur": "סאג'ור",
    "shavei zion": "שבי ציון",
    "eilabun": "עילבון",
    "beit alfa": "בית אלפא",
    "sha'ar efraim": "שער אפרים",
    "yavne'el": "יבנאל",
    "beit-gan": "בית גן",
    "beit gan": "בית גן",
    "srigim-li on": "שריגים לי און",
    "srigim li on": "שריגים לי און",
    "shomria": "שומריה",
    "neve ilan": "נווה אילן",
    "sapir": "ספיר",
    "givat brenner": "גבעת ברנר",
    "ma'alot tarshiha": "מעלות תרשיחא",
    "maalot tarshiha": "מעלות תרשיחא",
    "pardes hanna-karkur": "פרדס חנה כרכור",
    "beit yitshak sha'ar hefer": "בית יצחק שער חפר",
    "kefar sava": "כפר סבא",
    "qiryat shemona": "קריית שמונה",
    "kiryat shemona": "קריית שמונה",
    "kiryat gat": "קריית גת",
    "kiryat ata": "קריית אתא",
    "tirat carmel": "טירת כרמל",
    "giv'at shmuel": "גבעת שמואל",
    "givat shmuel": "גבעת שמואל",
    "acre": "עכו",
    "afula": "עפולה",
    "safed": "צפת",
    "tzur moshe": "צור משה",
    "kadima tzoran": "קדימה צורן",
    "yesud ha-ma'ala": "יסוד המעלה",
    "yesud hamaala": "יסוד המעלה",
    # Noisy / typo Hebrew locality fragments
    "גית הגלילית": "ג'ת",
    "ג'ל הגלילית": "ג'ת",
    "גת הגלילית": "ג'ת",
    "שפר הלבנה": "שפר",
    "מרגה": "מרג'ה",
    "מרג'ה": "מרג'ה",
    "תומר": "תומר",
    "תומר חממה": "תומר",
    "tomer": "תומר",
}

_FOREIGN = re.compile(
    r"\b(ukraine|украин|россия|russia|usa|united states|germany|france|poland)\b",
    re.I,
)
_POSTAL = re.compile(r"\b\d{5,7}\b")
_APOSTROPHE = re.compile(r"['`´’׳]")


def _norm_key(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).strip().lower()
    t = _APOSTROPHE.sub("", t)
    t = re.sub(r"[\-_/]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t


SETTLEMENT_ALIASES: dict[str, str] = {
    _norm_key(k): v for k, v in _SETTLEMENT_ALIASES_RAW.items()
}


@dataclass
class GeocodeResult:
    lat: float
    lon: float
    display_name: str
    is_city_level: bool
    city: str


def _in_region(lat: float, lon: float) -> bool:
    south, west, north, east = REGION_BBOX
    return south <= lat <= north and west <= lon <= east


def is_city_only_address(address: str) -> bool:
    """Heuristic: True when address looks like settlement name only."""
    text = address.strip()
    text = re.sub(r",?\s*(Israel|ישראל)\s*$", "", text, flags=re.IGNORECASE).strip()
    text = text.strip(" ,")
    if not text:
        return True
    if re.search(r"\d", text):
        return False
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) <= 1:
        return True
    return True  # multi-part, no digits → city / region


def extract_settlement(address: str) -> str:
    """Best-effort settlement / locality token from a free-form address."""
    text = address.strip()
    text = re.sub(r",?\s*(Israel|ישראל)\s*$", "", text, flags=re.IGNORECASE).strip()
    text = _POSTAL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if not text:
        return ""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2:
        # Prefer last non-numeric part
        for part in reversed(parts):
            if not re.fullmatch(r"\d+", part.replace(" ", "")):
                return part
    # "Street 5 City" without commas — take trailing words after house number
    m = re.search(
        r"^(?:.*?\d+[א-תA-Za-z]?\s+)(.+)$",
        text,
    )
    if m and len(m.group(1).split()) <= 6:
        return m.group(1).strip()
    return parts[-1] if parts else text


def apply_alias(name: str) -> str:
    key = _norm_key(name)
    if key in SETTLEMENT_ALIASES:
        return SETTLEMENT_ALIASES[key]
    # Drop trailing house numbers: "שפר הלבנה 129" → "שפר הלבנה"
    key_no_num = _norm_key(re.sub(r"\s+\d+[א-תA-Za-z]?\s*$", "", name))
    if key_no_num in SETTLEMENT_ALIASES:
        return SETTLEMENT_ALIASES[key_no_num]
    # First word only (e.g. "תומר חממה 50" → "תומר")
    first = _norm_key(name.split()[0]) if name.split() else ""
    if first in SETTLEMENT_ALIASES:
        return SETTLEMENT_ALIASES[first]
    # try without leading "ha-" / "the "
    key2 = re.sub(r"^(ha|the)\s+", "", key)
    return SETTLEMENT_ALIASES.get(key2, name)


def _strip_street_noise(name: str) -> str:
    t = name.strip()
    t = re.sub(
        r"\b(st|street|rd|road|ave|avenue|blvd|lane|dr|drive)\b\.?",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"^(רחוב|רח'|סמ'|סמטת)\s+", "", t)
    return re.sub(r"\s+", " ", t).strip(" ,")


def build_query_candidates(address: str) -> list[str]:
    """Ordered list of Nominatim queries to try."""
    raw = address.strip()
    if not raw or _FOREIGN.search(raw):
        return []

    cleaned = _POSTAL.sub("", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    # Fix glued Hebrew+number: פסוטה63 → פסוטה 63
    cleaned = re.sub(r"([\u0590-\u05FF])(\d)", r"\1 \2", cleaned)
    cleaned = re.sub(r"(\d)([\u0590-\u05FF])", r"\1 \2", cleaned)

    settlement = extract_settlement(cleaned)
    settlement_clean = _strip_street_noise(settlement) if settlement else ""
    aliased = apply_alias(settlement_clean or settlement) if settlement else ""
    no_apos = _APOSTROPHE.sub("", cleaned)
    no_apos_sett = _APOSTROPHE.sub("", settlement_clean or settlement) if settlement else ""

    candidates: list[str] = []

    def add(q: str) -> None:
        q = q.strip(" ,")
        if q and q not in candidates:
            candidates.append(q)

    # Prefer Hebrew / alias settlement early for West Bank & transliteration fixes
    if aliased and aliased != settlement:
        add(aliased)
        add(f"{aliased}, Israel")
    if settlement_clean and settlement_clean != settlement:
        add(settlement_clean)
        add(apply_alias(settlement_clean))

    add(cleaned)
    if "israel" not in cleaned.lower() and "ישראל" not in cleaned:
        add(f"{cleaned}, Israel")
    add(no_apos)
    if settlement:
        add(settlement)
        add(f"{settlement}, Israel")
        add(no_apos_sett)
    if aliased and settlement and settlement in cleaned and aliased != settlement:
        add(cleaned.replace(settlement, aliased))

    return candidates


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

    def _search(
        self,
        query: str,
        *,
        countrycodes: str | None,
        limit: int = 5,
        featuretype: str | None = None,
    ) -> list[dict[str, Any]]:
        self._throttle()
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "limit": limit,
            "viewbox": config.ISRAEL_VIEWBOX,
            "bounded": 0,
            "addressdetails": 1,
        }
        if countrycodes:
            params["countrycodes"] = countrycodes
        if featuretype:
            params["featuretype"] = featuretype
        try:
            r = self.session.get(
                f"{config.NOMINATIM_URL}/search",
                params=params,
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            log.warning("Nominatim failed for %r: %s", query, exc)
            return []

    def _pick_hit(
        self, results: list[dict[str, Any]], query: str
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = -1.0
        q_norm = _norm_key(_strip_street_noise(query))
        for hit in results:
            try:
                lat = float(hit["lat"])
                lon = float(hit["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not _in_region(lat, lon):
                continue
            cls = (hit.get("class") or "").lower()
            typ = (hit.get("type") or "").lower()
            name = _norm_key(hit.get("name") or hit.get("display_name") or "")
            score = 0.0
            if cls in ("place", "boundary"):
                score += 8
            if typ in (
                "city",
                "town",
                "village",
                "hamlet",
                "suburb",
                "neighbourhood",
                "residential",
            ):
                score += 6
            if cls == "highway":
                score += 1
            if cls == "building":
                score += 2
            if cls == "amenity":
                score -= 6
            if q_norm and (q_norm in name or name.startswith(q_norm[: max(3, len(q_norm) // 2)])):
                score += 5
            # Hebrew queries: prefer results whose display contains Hebrew letters
            if re.search(r"[\u0590-\u05FF]", query) and re.search(
                r"[\u0590-\u05FF]", hit.get("display_name") or ""
            ):
                score += 2
            try:
                score += float(hit.get("importance") or 0) * 3
            except (TypeError, ValueError):
                pass
            if score > best_score:
                best_score = score
                best = hit
        return best

    def _hit_to_result(
        self, hit: dict[str, Any], original: str, city_level: bool
    ) -> GeocodeResult:
        addr = hit.get("address") or {}
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or addr.get("suburb")
            or addr.get("hamlet")
            or ""
        )
        return GeocodeResult(
            lat=float(hit["lat"]),
            lon=float(hit["lon"]),
            display_name=hit.get("display_name") or original,
            is_city_level=city_level,
            city=city,
        )

    def geocode(self, address: str) -> GeocodeResult | None:
        query = address.strip()
        if not query:
            return None
        if _FOREIGN.search(query):
            log.info("Skip foreign address: %s", query[:80])
            return None

        city_only = is_city_only_address(query)
        candidates = build_query_candidates(query)
        if not candidates:
            return None

        sett = extract_settlement(query)
        sett_clean = _strip_street_noise(sett) if sett else ""
        aliased_sett = apply_alias(sett_clean or sett) if sett else ""
        settlement_queries = {
            x
            for x in (
                sett,
                sett_clean,
                aliased_sett,
                f"{sett}, Israel" if sett else "",
                f"{sett_clean}, Israel" if sett_clean else "",
                f"{aliased_sett}, Israel" if aliased_sett else "",
            )
            if x
        }

        # 1) Settlement aliases / Hebrew without IL filter (West Bank + avoid street collisions)
        # 2) Full address with IL
        # 3) Full address without country filter
        # 4) Explicit featuretype=settlement
        passes: list[tuple[str | None, str | None, bool, bool]] = [
            (None, "settlement", True, True),  # settlement-only candidates
            ("il", None, False, False),
            (None, None, False, False),
            (None, "settlement", True, False),
        ]

        for countrycodes, featuretype, force_city, settlement_only in passes:
            pool = (
                [c for c in candidates if c in settlement_queries or c == aliased_sett]
                if settlement_only
                else candidates
            )
            if not pool:
                continue
            for cand in pool:
                results = self._search(
                    cand,
                    countrycodes=countrycodes,
                    limit=5,
                    featuretype=featuretype,
                )
                hit = self._pick_hit(results, cand)
                if hit:
                    matched_city = (
                        force_city
                        or city_only
                        or cand in settlement_queries
                        or (
                            (hit.get("class") or "") == "place"
                            and cand != candidates[0]
                        )
                    )
                    log.info(
                        "Geocoded %r via %r (cc=%s ft=%s) -> %s",
                        query[:60],
                        cand[:60],
                        countrycodes or "*",
                        featuretype or "-",
                        (hit.get("display_name") or "")[:70],
                    )
                    return self._hit_to_result(hit, query, matched_city)

        log.warning("No geocode for %r", address)
        return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))

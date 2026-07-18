"""Pipedrive API client: fetch persons with addresses."""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests

from . import config

log = logging.getLogger(__name__)

# Known Hebrew custom-field labels that may hold the address
ADDRESS_LABEL_HINTS = ("כתובת", "address", "Address", "כתובת מלאה")
ADDRESS_SUBFIELD_SUFFIXES = (
    "lat",
    "long",
    "longitude",
    "subpremise",
    "street_number",
    "route",
    "sublocality",
    "locality",
    "admin_area_level_1",
    "admin_area_level_2",
    "country",
    "postal_code",
)


def _is_address_component_key(key: str) -> bool:
    return any(key.endswith(f"_{suf}") for suf in ADDRESS_SUBFIELD_SUFFIXES)


class PipedriveClient:
    def __init__(
        self,
        token: str | None = None,
        domain: str | None = None,
    ) -> None:
        self.token = token or config.require_token()
        self.domain = domain or config.PIPEDRIVE_COMPANY_DOMAIN
        self.base = f"https://{self.domain}.pipedrive.com/api/v1"
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._address_field_keys: list[str] | None = None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        p = dict(params or {})
        p["api_token"] = self.token
        url = f"{self.base}{path}"
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                r = self.session.get(url, params=p, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = min(60, 2 ** attempt)
                    log.warning(
                        "Pipedrive HTTP %s on %s, retry in %ss",
                        r.status_code,
                        path,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                if not data.get("success", True) and data.get("error"):
                    raise RuntimeError(f"Pipedrive error: {data.get('error')}")
                return data
            except requests.RequestException as exc:
                last_exc = exc
                wait = min(60, 2 ** attempt)
                log.warning("Pipedrive request failed (%s), retry in %ss", exc, wait)
                time.sleep(wait)
        raise RuntimeError(f"Pipedrive request failed after retries: {last_exc}")

    def discover_address_fields(self) -> list[str]:
        """Return person field keys that look like address fields (main keys only)."""
        if self._address_field_keys is not None:
            return self._address_field_keys

        keys: list[str] = []
        try:
            data = self._get("/personFields")
            for field in data.get("data") or []:
                name = (field.get("name") or "").strip()
                key = field.get("key") or ""
                ftype = (field.get("field_type") or "").lower()
                if not key:
                    continue
                if key.endswith("_formatted_address"):
                    keys.insert(0, key)
                    continue
                if _is_address_component_key(key):
                    continue
                if (
                    key == "postal_address"
                    or name in ADDRESS_LABEL_HINTS
                    or ftype == "address"
                    or "כתובת" in name
                ):
                    keys.append(key)
        except Exception as exc:
            log.warning("Could not list personFields: %s", exc)

        if "postal_address" not in keys:
            keys.append("postal_address")

        seen: set[str] = set()
        ordered: list[str] = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                ordered.append(k)

        self._address_field_keys = ordered
        log.info("Address field keys: %s", ordered)
        return ordered

    def iter_persons(self, limit: int = 500) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            data = self._get(
                "/persons",
                {"start": start, "limit": limit},
            )
            items = data.get("data") or []
            if not items:
                break
            for person in items:
                yield person
            more = (data.get("additional_data") or {}).get("pagination") or {}
            if not more.get("more_items_in_collection"):
                break
            start = more.get("next_start", start + limit)

    @staticmethod
    def _extract_address_value(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, dict):
            for k in ("value", "formatted_address", "address"):
                v = raw.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            # Pipedrive address object often has street_number, route, locality
            parts = [
                raw.get("route") or raw.get("street_address"),
                raw.get("street_number"),
                raw.get("sublocality") or raw.get("locality") or raw.get("city"),
                raw.get("admin_area_level_1"),
                raw.get("country"),
            ]
            text = ", ".join(str(p).strip() for p in parts if p)
            return text.strip(" ,")
        return str(raw).strip()

    def get_person_address(self, person: dict[str, Any], field_keys: list[str]) -> str:
        for key in field_keys:
            # Prefer Pipedrive's formatted_address companion key
            formatted_key = f"{key}_formatted_address"
            if formatted_key in person and person[formatted_key]:
                addr = self._extract_address_value(person[formatted_key])
                if addr:
                    return addr
            if key in person and person[key]:
                addr = self._extract_address_value(person[key])
                if addr:
                    return addr
        return ""

    def iter_persons_with_address(self) -> Iterator[dict[str, Any]]:
        keys = self.discover_address_fields()
        for person in self.iter_persons():
            address = self.get_person_address(person, keys)
            if not address:
                continue
            yield {
                "person_id": person.get("id"),
                "name": person.get("name") or "",
                "address": address,
            }

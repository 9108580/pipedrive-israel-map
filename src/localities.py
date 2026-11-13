"""Canonical settlement identity, independent of geocoding/roof precision.

Only registered names and reviewed aliases may become settlement IDs. Raw CRM
addresses take precedence over lossy Google-formatted address companions.
"""
from __future__ import annotations
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def name_key(value: str) -> str:
    text = unicodedata.normalize('NFKC', value).casefold()
    return ''.join(c for c in text if c.isalnum() and not unicodedata.combining(c))


@lru_cache(maxsize=1)
def registry() -> tuple[dict, dict]:
    rows = json.loads((ROOT / 'data/localities.json').read_text(encoding='utf-8'))['localities']
    by_id = {r['id']: r for r in rows}
    names: dict[str, set[str]] = {}
    for row in rows:
        for alias in [row['name'], *row['aliases']]:
            names.setdefault(name_key(alias), set()).add(row['id'])
    return by_id, {k: next(iter(v)) for k, v in names.items() if len(v) == 1}


def lookup(name: str) -> str | None:
    return registry()[1].get(name_key(name)) if name else None


def clean_address(text: str) -> str:
    text = re.sub(r'(?:,?\s*)(?:Israel|ישראל|Израиль)\s*$', '', text.strip(), flags=re.I)
    return re.sub(r'\s+', ' ', text).strip(' ,')


def address_locality(text: str) -> str | None:
    text = clean_address(text)
    direct = lookup(text)
    if direct:
        return direct
    parts = [p.strip() for p in text.split(',') if p.strip()]
    # An explicit city segment outranks a street that happens to share a name.
    for part in reversed(parts):
        if lookup(part):
            return lookup(part)
    if len(parts) == 1:
        core = re.sub(r'\s*\d+\s*[א-תA-Za-z]?\s*$', '', text).strip()
        if lookup(core):
            return lookup(core)
    # City after the house number: "יוסף בורג 11 קריית טבעון".
    tail = re.split(r'\d+\s*[א-ת]?(?:\s|$)', text)[-1].strip(' ,')
    if tail and lookup(tail):
        return lookup(tail)
    # Explicit locality at either end of a handwritten address. Longest wins;
    # never inspect arbitrary interior words, street labels or geocoder output.
    words = text.split()
    for size in range(min(5, len(words)-1), 0, -1):
        prefix = ' '.join(words[:size]).strip(' ,')
        suffix = ' '.join(words[-size:]).strip(' ,')
        if lookup(suffix):
            return lookup(suffix)
        if lookup(prefix) and not re.match(r'^(?:רחוב|רח[\x27׳]|דרך|שדרות|st\b|street\b)', text, re.I):
            return lookup(prefix)
    # Settlement prefixes used in CRM, not part of the locality name.
    shorter = re.sub(r'^(?:מושב|קיבוץ|ישוב|יישוב)\s+', '', text)
    if shorter != text:
        return address_locality(shorter)
    return None


@lru_cache(maxsize=1)
def reviewed_addresses() -> dict:
    path = ROOT / 'data/locality_overrides.json'
    return json.loads(path.read_text(encoding='utf-8')).get('persons', {}) if path.exists() else {}


def resolve_locality(*, raw: str = '', formatted: str = '', locality: str = '', person_id: int | None = None) -> dict:
    # Overrides are exact-address guarded and documented, never ID-only guesses.
    override = reviewed_addresses().get(str(person_id))
    if override and override['raw'].strip() == raw.strip():
        lid = override.get('locality_id')
        if lid:
            return {'id': lid, 'name': registry()[0][lid]['name'], 'source': 'reviewed_crm', 'reason': override['evidence']}
        return {'id': None, 'name': '', 'source': 'unresolved', 'reason': override['evidence']}
    for source, lid in [('crm_raw', address_locality(raw)), ('crm_locality', lookup(locality)), ('crm_formatted', address_locality(formatted))]:
        if lid:
            return {'id': lid, 'name': registry()[0][lid]['name'], 'source': source, 'reason': ''}
    return {'id': None, 'name': '', 'source': 'unresolved', 'reason': 'CRM address has no unambiguous registered locality'}

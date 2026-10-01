"""
services/npi_source.py — CMS NPPES NPI Registry as a lead source.

The National Plan & Provider Enumeration System (NPPES) publishes the NPI
Registry: every US healthcare provider/organization, with practice name, full
address, phone, and taxonomy (specialty). It's an official, free, key-less API
(no scraping, no blocking) — by far the largest, cleanest source for any
medical/healthcare niche.

  API: https://npiregistry.cms.hhs.gov/api/  (version 2.1)
  - enumeration_type=NPI-2  -> organizations (businesses), best for B2B leads
  - limit<=200 per call, skip<=1000 -> up to ~1200 results per (niche, city)
  - taxonomy_description supports wildcards (e.g. "*clinic*", "*dentist*")

Only activates for US + healthcare-ish niches; returns [] otherwise so it never
pollutes non-medical niches. Returns canonical lead dicts (see sources.LEAD_FIELDS).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API = "https://npiregistry.cms.hhs.gov/api/"
TIMEOUT = 20

# Niche keywords that indicate a healthcare niche worth querying NPPES for.
_MEDICAL_HINTS = (
    "medical", "clinic", "health", "doctor", "physician", "dentist", "dental",
    "hospital", "urgent care", "pediatric", "therapy", "therapist", "chiropract",
    "psych", "mental", "rehab", "nursing", "care", "surgery", "surgeon",
    "optometr", "optical", "dermatolog", "cardiolog", "orthoped", "pharmacy",
    "wellness", "diagnostic", "radiology", "veterinar", "obgyn", "gynecolog",
    "podiatr", "acupunctur", "chiropractor", "physiotherap", "imaging", "lab",
    "hospice", "home health", "behavioral", "counseling", "midwife", "audiolog",
)

# Generic words to drop when deriving a taxonomy keyword from the niche.
_STOPWORDS = {
    "the", "and", "of", "in", "a", "best", "top", "local", "affordable", "near",
    "medical", "business", "businesses", "services", "service", "company",
    "near me", "good", "cheap",
}

# Common US state name -> 2-letter code (locations may use either form).
_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC",
}


def is_medical_niche(niche: str) -> bool:
    n = (niche or "").lower()
    return any(h in n for h in _MEDICAL_HINTS)


def _parse_location(location: str) -> tuple[str, str]:
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    city = parts[0] if parts else ""
    state = ""
    if len(parts) > 1:
        st = parts[1].strip()
        if len(st) == 2:
            state = st.upper()
        else:
            state = _STATE_ABBR.get(st.lower(), "")
    # Strip leading directional modifiers the tiler adds ("Downtown Los Angeles").
    city = re.sub(r"^(downtown|north|south|east|west|central)\s+", "", city,
                  flags=re.IGNORECASE).strip()
    return city, state


def _taxonomy_term(niche: str) -> str:
    """Derive a taxonomy wildcard keyword from the niche (best-effort)."""
    words = [w for w in re.findall(r"[a-z]+", (niche or "").lower())
             if w not in _STOPWORDS and len(w) >= 3]
    # Prefer the most specific (last) meaningful word, e.g. "pediatric clinic" -> clinic,
    # "dentist" -> dentist, "urgent care" -> care.
    return words[-1] if words else ""


def _norm(res: dict) -> Optional[dict]:
    basic = res.get("basic", {}) or {}
    name = (basic.get("organization_name")
            or " ".join(filter(None, [basic.get("first_name"), basic.get("last_name")])))
    name = (name or "").strip()
    if not name:
        return None
    addrs = res.get("addresses", []) or []
    loc = next((a for a in addrs if a.get("address_purpose") == "LOCATION"),
               addrs[0] if addrs else {})
    addr_parts = [loc.get("address_1"), loc.get("address_2"),
                  loc.get("city"), loc.get("state"), loc.get("postal_code")]
    address = ", ".join(p for p in addr_parts if p) or None
    phone = loc.get("telephone_number")
    tax = next((t for t in res.get("taxonomies", []) if t.get("primary")),
               (res.get("taxonomies") or [{}])[0])
    return {
        "name": name,
        "address": address,
        "phone": phone,
        "website": None,
        "category": tax.get("desc"),
        "source": "npi",
        "source_url": f"https://npiregistry.cms.hhs.gov/provider-view/{res.get('number')}"
                      if res.get("number") else None,
    }


# Per-process cache: directional tiles ("Downtown LA", "North LA", …) all parse
# to the same NPI city, so without this we'd fire the same paginated query ~10x
# per job. Keyed by (city, state, taxonomy term, limit).
_CACHE: dict = {}
_CACHE_MAX = 256


def _query(params: dict) -> list[dict]:
    try:
        r = httpx.get(API, params=params, timeout=TIMEOUT)
        if r.status_code >= 400:
            return []
        return r.json().get("results") or []
    except Exception as e:
        logger.info("NPI query failed (%s): %s", params.get("skip"), e)
        return []


def search_npi(niche: str, location: str, limit: int = 50, country: str = "us") -> list[dict]:
    """Return up to `limit` US healthcare organizations matching the niche/location.

    Strategy: paginate NPI-2 (organizations) by city/state, filtered by a taxonomy
    keyword derived from the niche. If the taxonomy filter is too narrow (no hits),
    fall back to an unfiltered city/state sweep (all healthcare orgs there).
    """
    if (country or "us").lower() != "us" or not is_medical_niche(niche):
        return []

    city, state = _parse_location(location)
    if not (city or state):
        return []

    tax_term = _taxonomy_term(niche)

    cache_key = (city.lower(), state.upper(), tax_term, limit)
    if cache_key in _CACHE:
        return [dict(r) for r in _CACHE[cache_key]]

    out: list[dict] = []
    seen: set = set()

    def sweep(use_taxonomy: bool):
        for skip in range(0, 1001, 200):   # API caps skip at 1000
            if len(out) >= limit:
                return
            params = {
                "version": "2.1", "enumeration_type": "NPI-2",
                "limit": 200, "skip": skip,
            }
            if city:
                params["city"] = city
            if state:
                params["state"] = state
            if use_taxonomy and tax_term:
                params["taxonomy_description"] = f"*{tax_term}*"
            results = _query(params)
            if not results:
                return
            for res in results:
                rec = _norm(res)
                if not rec:
                    continue
                phone7 = re.sub(r"\D", "", rec.get("phone") or "")[-7:]
                key = (rec["name"].lower(), phone7)
                if key in seen:
                    continue
                seen.add(key)
                out.append(rec)
                if len(out) >= limit:
                    return
            if len(results) < 200:
                return

    sweep(use_taxonomy=True)
    if not out:                      # taxonomy too narrow — broaden to all healthcare
        sweep(use_taxonomy=False)

    result = out[:limit]
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[cache_key] = result
    logger.info("NPI source returned %d businesses for '%s' in '%s'",
                len(result), niche, location)
    return [dict(r) for r in result]

"""
services/registration.py — Legal-entity / registration lookup (goal 4b).

Confirms a business is a real, active legal entity and pulls its legal name,
type, status, registration date, and registered agent. Feeds accuracy (goal 5):
an active registration corroborates the lead (+confidence); a *dissolved* one is
a hard reject signal.

Sources, in the order tried (all free, key-gated, off by default):
  1. SAM.gov Entity Management API — free key (SAM_API_KEY), 1k req/day. Only
     covers entities registered for US federal work → treat as bonus corroboration.
  2. OpenCorporates — free tier (~500/mo). Broad US coverage across state registries.

Per-state Secretary-of-State scrapers are the real registry but have NO unified
free API and each is bespoke, brittle HTML — deliberately NOT built speculatively
(YAGNI). `sos_lookup` is the documented extension point: add one state at a time,
behind the same interface, when you actually sell into that state.

Everything here soft-fails: no key / network error / no match → returns None, and
the caller leaves the lead's registration fields blank.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 12


def _norm_status(raw: str | None) -> Optional[str]:
    """Normalise a registry's status wording to active | inactive | dissolved."""
    if not raw:
        return None
    s = raw.strip().lower()
    if any(k in s for k in ("active", "good standing", "current", "registered")):
        return "active"
    if any(k in s for k in ("dissolved", "cancelled", "canceled", "revoked",
                            "terminated", "withdrawn", "expired")):
        return "dissolved"
    return "inactive"


def _parse_sam(entity: dict) -> dict:
    """Extract fields from a SAM.gov entity record (entityData[0])."""
    reg = entity.get("entityRegistration", {}) or {}
    core = entity.get("coreData", {}) or {}
    return {
        "legal_name": reg.get("legalBusinessName"),
        "entity_type": (core.get("entityInformation", {}) or {}).get("entityStructureDesc"),
        "entity_status": _norm_status(reg.get("registrationStatus")),
        "registration_date": reg.get("registrationDate"),
        "registered_agent": None,
        "registry_source": "sam_gov",
    }


def sam_gov_lookup(name: str, api_key: str) -> Optional[dict]:
    """Look a business up in SAM.gov by legal name. Returns a registration dict
    or None. Requires a free SAM_API_KEY."""
    if not name or not api_key:
        return None
    try:
        r = httpx.get(
            "https://api.sam.gov/entity-information/v3/entities",
            params={"api_key": api_key, "legalBusinessName": name,
                    "includeSections": "entityRegistration,coreData"},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        entities = r.json().get("entityData") or []
        if not entities:
            return None
        return _parse_sam(entities[0])
    except Exception as e:  # noqa: BLE001
        logger.info("SAM.gov lookup failed for '%s': %s", name, e)
        return None


def _parse_opencorporates(company: dict) -> dict:
    return {
        "legal_name": company.get("name"),
        "entity_type": company.get("company_type"),
        "entity_status": _norm_status(company.get("current_status")
                                      or ("dissolved" if company.get("dissolution_date") else "active")),
        "registration_date": company.get("incorporation_date"),
        "registered_agent": (company.get("agent_name")),
        "registry_source": "opencorporates",
    }


def opencorporates_lookup(name: str, state: str | None = None,
                          api_key: str = "") -> Optional[dict]:
    """Search OpenCorporates for a US company by name (optionally scoped to a
    state jurisdiction like 'us_ca'). Returns a registration dict or None."""
    if not name:
        return None
    params = {"q": name, "order": "score"}
    if state:
        params["jurisdiction_code"] = f"us_{state.lower()}"
    if api_key:
        params["api_token"] = api_key
    try:
        r = httpx.get("https://api.opencorporates.com/v0.4/companies/search",
                      params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        companies = (r.json().get("results", {}) or {}).get("companies", []) or []
        if not companies:
            return None
        return _parse_opencorporates(companies[0]["company"])
    except Exception as e:  # noqa: BLE001
        logger.info("OpenCorporates lookup failed for '%s': %s", name, e)
        return None


def sos_lookup(name: str, state: str) -> Optional[dict]:
    """Extension point for per-state Secretary-of-State registries. Each state is
    a bespoke scraper (no unified free API) — implement one at a time, behind this
    signature, returning the same dict shape. Intentionally a no-op until a state
    you sell into is actually added. # ponytail: build per-state on real demand."""
    return None


def lookup_registration(name: str, state: str | None = None) -> Optional[dict]:
    """Resolve a business's registration from the configured sources, best-first.
    Returns a registration dict (see _parse_*), or None if nothing matched.
    Off unless ENABLE_REGISTRATION_LOOKUP is set."""
    try:
        from backend.config import settings as s
        if not getattr(s, "ENABLE_REGISTRATION_LOOKUP", False):
            return None
        sam_key = getattr(s, "SAM_API_KEY", "") or ""
        oc_key = getattr(s, "OPENCORPORATES_API_KEY", "") or ""
    except Exception:
        return None

    # SoS (most authoritative) → SAM.gov → OpenCorporates aggregator.
    for fn in (
        lambda: sos_lookup(name, state) if state else None,
        lambda: sam_gov_lookup(name, sam_key) if sam_key else None,
        lambda: opencorporates_lookup(name, state, oc_key),
    ):
        try:
            reg = fn()
        except Exception:
            reg = None
        if reg and reg.get("legal_name"):
            return reg
    return None


if __name__ == "__main__":
    assert _norm_status("Active (Good Standing)") == "active"
    assert _norm_status("DISSOLVED") == "dissolved"
    assert _norm_status("suspended") == "inactive"
    assert _norm_status(None) is None
    assert _parse_sam({"entityRegistration": {"legalBusinessName": "ACME LLC",
                       "registrationStatus": "Active", "registrationDate": "2019-01-01"}}
                      )["entity_status"] == "active"
    print("registration self-check OK")

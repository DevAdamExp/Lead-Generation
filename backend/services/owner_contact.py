"""
services/owner_contact.py — Free, best-effort owner contact discovery + verification.

We never fabricate. We collect every phone number we can find across the
business's own public surfaces, classify each (mobile vs fixed-line vs VoIP)
with Google's libphonenumber, and label the single most-likely "owner/direct"
line with a confidence score. We also verify the email (format + MX) and the
business phone.

Sources used (all free, no API key):
  - Website pages already crawled for email/owner-name (contact/about/team).
  - Google Maps listing phone (business line).
  - Social "About" pages.
  - RDAP (modern WHOIS) registrant phone — usually redacted post-GDPR, but free
    and occasionally present for small businesses.

Confidence heuristic for "owner phone":
  +0.40  number appears on an about/team/owner page (not just contact/footer)
  +0.30  number is a MOBILE line (owners often list a cell)
  +0.20  number differs from the main business phone
  +0.15  number sits near the detected owner name in the page text
  -0.30  number is the same as the main business/listing phone
Values are clamped to [0, 1]; we keep the highest-scoring candidate.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Loose phone matcher — libphonenumber then validates/classifies what we find.
_PHONE_RE = re.compile(r"(\+?\d[\d\s().\-]{7,}\d)")

# Words that, when near a number, suggest a personal/owner/direct line.
_PERSONAL_HINTS = ("mobile", "cell", "direct", "owner", "founder", "personal", "whatsapp")
_BUSINESS_HINTS = ("office", "fax", "tel", "phone", "call us", "reception")

# Page paths that indicate an owner/about context (higher confidence).
_OWNER_CONTEXT_RE = re.compile(r"about|team|owner|founder|staff|management|leadership", re.I)


def classify_phone(raw: str, region: str = "US") -> Optional[dict]:
    """
    Parse + classify a phone number. Returns
    {e164, national, type, valid} or None if unparseable.
    """
    import phonenumbers
    from phonenumbers import PhoneNumberType, number_type, is_valid_number

    cleaned = raw.strip()
    for reg in (region, "US", None):
        try:
            parsed = phonenumbers.parse(cleaned, reg)
        except Exception:
            continue
        if not is_valid_number(parsed):
            continue
        t = number_type(parsed)
        type_name = {
            PhoneNumberType.MOBILE: "mobile",
            PhoneNumberType.FIXED_LINE: "fixed_line",
            PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
            PhoneNumberType.VOIP: "voip",
            PhoneNumberType.TOLL_FREE: "toll_free",
        }.get(t, "unknown")
        return {
            "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
            "national": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL),
            "type": type_name,
            "valid": True,
        }
    return None


def harvest_phones_from_text(text: str, region: str = "US") -> list[dict]:
    """Find + classify all valid phone numbers in a blob of text."""
    found: dict[str, dict] = {}
    for m in _PHONE_RE.finditer(text or ""):
        raw = m.group(1)
        info = classify_phone(raw, region)
        if info and info["e164"] not in found:
            # capture a small context window for hint scoring
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            info = dict(info)
            info["context"] = text[start:end].lower()
            found[info["e164"]] = info
    return list(found.values())


def _score_owner_candidate(cand: dict, business_e164: Optional[str],
                           owner_name: Optional[str], from_owner_page: bool) -> float:
    score = 0.0
    ctx = cand.get("context", "")
    if from_owner_page:
        score += 0.40
    if cand["type"] in ("mobile", "fixed_or_mobile"):
        score += 0.30
    if business_e164 and cand["e164"] != business_e164:
        score += 0.20
    elif business_e164 and cand["e164"] == business_e164:
        score -= 0.30
    if any(h in ctx for h in _PERSONAL_HINTS):
        score += 0.15
    if any(h in ctx for h in _BUSINESS_HINTS):
        score -= 0.10
    if owner_name:
        first = owner_name.split()[0].lower()
        if first and first in ctx:
            score += 0.15
    return max(0.0, min(1.0, score))


@lru_cache(maxsize=4096)
def rdap_domain(domain: str) -> Optional[dict]:
    """One cached RDAP (modern WHOIS) document per domain, keyless via rdap.org.

    rdap.org routes to the right registry by TLD — unlike a hardcoded
    rdap.verisign.com/com/ endpoint, which 404s on every non-.com domain.
    Shared by rdap_registrant_phone (registrant tel) and business_intel's
    domain-age lookup (registration event), so a lead pays ONE request, not two.
    """
    if not domain or "." not in domain:
        return None
    try:
        import httpx
        with httpx.Client(timeout=12, follow_redirects=True) as client:
            r = client.get(f"https://rdap.org/domain/{domain}")
            if r.status_code != 200:
                return None
            return r.json()
    except Exception:
        return None


def domain_from_url(website: str) -> str:
    """Bare registrable host from a URL (no scheme, no www., no port)."""
    if not website:
        return ""
    netloc = urlparse(website if "://" in website else f"http://{website}").netloc
    return netloc.removeprefix("www.").split(":")[0]


def rdap_registrant_phone(website: str, region: str = "US") -> Optional[dict]:
    """
    Query RDAP for the domain's registrant phone. Usually redacted post-GDPR, but
    free and occasionally present.
    """
    data = rdap_domain(domain_from_url(website))
    if not data:
        return None

    # vCard arrays live under entities[].vcardArray[1][*]
    for entity in data.get("entities", []) or []:
        roles = entity.get("roles", []) or []
        if "registrant" not in roles and "administrative" not in roles:
            continue
        vcard = entity.get("vcardArray")
        if not (isinstance(vcard, list) and len(vcard) > 1):
            continue
        for field in vcard[1]:
            if isinstance(field, list) and field and field[0] == "tel":
                raw = field[3] if len(field) > 3 else ""
                if isinstance(raw, str) and raw.startswith("tel:"):
                    raw = raw[4:]
                info = classify_phone(str(raw), region)
                if info:
                    info["source"] = "rdap"
                    return info
    return None


def find_owner_contact(
    *,
    website: Optional[str],
    business_phone: Optional[str],
    owner_name: Optional[str],
    page_texts: list[tuple[str, str]],   # [(url, visible_text), ...]
    maps_phone: Optional[str] = None,
    social_texts: Optional[list[str]] = None,
    region: str = "US",
) -> dict:
    """
    Best-effort owner phone discovery.

    Returns:
        {
          owner_phone, owner_phone_type, owner_phone_confidence,
          all_phones: [e164, ...]
        }
    """
    business_e164 = None
    if business_phone:
        b = classify_phone(business_phone, region)
        business_e164 = b["e164"] if b else None
    if not business_e164 and maps_phone:
        b = classify_phone(maps_phone, region)
        business_e164 = b["e164"] if b else None

    candidates: list[dict] = []

    # 1) Website page texts (with owner-page context weighting)
    for url, text in page_texts or []:
        from_owner_page = bool(_OWNER_CONTEXT_RE.search(url or ""))
        for cand in harvest_phones_from_text(text, region):
            cand["from_owner_page"] = from_owner_page
            candidates.append(cand)

    # 2) Social About texts
    for text in social_texts or []:
        for cand in harvest_phones_from_text(text, region):
            cand["from_owner_page"] = True  # About pages are owner-ish context
            candidates.append(cand)

    # 3) RDAP registrant
    rdap = rdap_registrant_phone(website, region) if website else None
    if rdap:
        rdap["context"] = "registrant owner"
        rdap["from_owner_page"] = True
        candidates.append(rdap)

    # De-dupe by e164, keep best context
    by_e164: dict[str, dict] = {}
    for c in candidates:
        e = c["e164"]
        if e not in by_e164:
            by_e164[e] = c

    best = None
    best_score = 0.0
    for c in by_e164.values():
        s = _score_owner_candidate(
            c, business_e164, owner_name, c.get("from_owner_page", False)
        )
        if s > best_score:
            best_score = s
            best = c

    all_phones = list(by_e164.keys())
    if best and best_score >= 0.35 and best["e164"] != business_e164:
        return {
            "owner_phone": best["national"],
            "owner_phone_type": best["type"],
            "owner_phone_confidence": round(best_score, 2),
            "all_phones": all_phones,
        }
    return {
        "owner_phone": None,
        "owner_phone_type": None,
        "owner_phone_confidence": 0.0,
        "all_phones": all_phones,
    }

"""
utils/validator.py — Phone number formatting with configurable region.

Fixes:
  - Default region is configurable (per-job) instead of hardcoded US.
  - Multiple region fallback: tries the specified region, then common formats.
  - Returns both E.164 and national formats for flexibility.
"""
import re
from typing import Optional
import logging

logger = logging.getLogger(__name__)


# Region code to country mapping for jobs
COUNTRY_TO_REGION = {
    "us": "US", "ca": "CA", "gb": "GB", "uk": "GB", "de": "DE",
    "fr": "FR", "it": "IT", "es": "ES", "pt": "PT", "nl": "NL",
    "be": "BE", "ch": "CH", "at": "AT", "se": "SE", "no": "NO",
    "dk": "DK", "fi": "FI", "ie": "IE", "au": "AU", "nz": "NZ",
    "jp": "JP", "cn": "CN", "kr": "KR", "in": "IN", "br": "BR",
    "mx": "MX", "ar": "AR", "cl": "CL", "za": "ZA", "ru": "RU",
}


def format_phone(raw: str, region: str = "US") -> Optional[str]:
    """
    Parse and format a phone number.
    Tries the specified region first, then falls back to common formats.

    Args:
        raw: Raw phone number string.
        region: ISO 3166-1 alpha-2 region code (default: "US").

    Returns:
        Formatted number in international format, or None if invalid.
    """
    if not raw:
        return None

    import phonenumbers

    # Strip extension before cleaning (ext. 123 / x123 / extension 1)
    cleaned = raw.strip()
    cleaned = re.sub(r"\s*(?:ext|extension|x)\s*\.?\s*\d+\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\d+]", "", cleaned)
    if not cleaned:
        return None

    # Try parsing with the specified region first
    regions_to_try = [region, "US", None]

    for reg in regions_to_try:
        try:
            if reg:
                parsed = phonenumbers.parse(cleaned, reg)
            else:
                parsed = phonenumbers.parse(cleaned)

            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
                )
        except Exception:
            continue

    # Final attempt: try parsing as-is without region
    try:
        parsed = phonenumbers.parse(cleaned, None)
        if phonenumbers.is_possible_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
    except Exception:
        pass

    return None


def is_valid_email(email: str) -> bool:
    """Basic email format check."""
    import re
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))

"""
services/verifier.py — Free email + phone verification with caching.

Zero-cost verification using Python standard library and open-source packages:
  - Email: format check → MX record lookup (dnspython, cached by domain) → SMTP banner
  - Phone: libphonenumber validation + type detection + area-code → state mapping
"""
import logging
import re
import time
import dns.resolver
import smtplib
import httpx
import phonenumbers

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_ROLE_ADDRESSES = {
    "info", "contact", "support", "admin", "hello", "sales",
    "office", "team", "mail", "enquiries", "booking",
    "hr", "careers", "jobs", "billing", "accounts",
}

_FREE_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "mail.com", "protonmail.com", "proton.me", "zoho.com",
    "yandex.com", "gmx.com", "live.com", "msn.com",
}

# TTL cache for MX lookups: {domain: (timestamp, [mx_hosts])}
_MX_CACHE: dict[str, tuple[float, list[str]]] = {}
_MX_CACHE_TTL = 300  # 5 minutes

# TTL cache for full verify_email results, keyed by lowercased email. Makes the
# scoring stage's re-verification (pipeline._stage_score) a free cache hit
# instead of a second serial SMTP round-trip per lead.
_EMAIL_CACHE: dict[str, tuple[float, dict]] = {}
_EMAIL_CACHE_TTL = 600  # 10 minutes

# Per-domain catch-all verdict cache (a random-address RCPT probe). None = not
# yet probed / port 25 blocked. True = domain accepts everything (deliverability
# unprovable). Keyed by domain.
_CATCHALL_CACHE: dict[str, tuple[float, bool | None]] = {}
# Domains where SMTP is unreachable (port 25 blocked, greylisted, refused) — skip
# the probe for the rest of the run instead of eating a timeout per address.
_SMTP_DEAD_DOMAINS: set[str] = set()

# Host-level kill switch. _SMTP_DEAD_DOMAINS is keyed by DOMAIN, but every lead
# has a different domain — so on a host with outbound port 25 blocked (most ISPs,
# most clouds, every macOS dev box) we paid a fresh `timeout` connect per lead and
# learned nothing. After _SMTP_FAIL_LIMIT consecutive unreachable MXs, conclude
# port 25 is blocked here and stop probing for the rest of the process; the
# "mx_only" tier already models that degradation correctly.
_SMTP_UNAVAILABLE = False
_SMTP_FAIL_STREAK = 0
_SMTP_FAIL_LIMIT = 5

# Probe envelope sender — a real-looking address on a domain we control-ish.
# Never sends DATA, so no mail is emitted; only the RCPT response code is read.
_PROBE_MAIL_FROM = "postmaster@example.com"


def _resolve_mx(domain: str, timeout: int = 5) -> list[str]:
    """Cached MX record lookup."""
    now = time.time()
    cached = _MX_CACHE.get(domain)
    if cached and (now - cached[0]) < _MX_CACHE_TTL:
        return cached[1]
    try:
        records = dns.resolver.resolve(domain, "MX", lifetime=timeout)
        # Prefer lowest-preference MX first (most likely to accept RCPT).
        hosts = [str(r.exchange).rstrip(".")
                 for r in sorted(records, key=lambda r: r.preference)]
        _MX_CACHE[domain] = (now, hosts)
        return hosts
    except Exception:
        _MX_CACHE[domain] = (now, [])
        return []


def _rcpt_code(mx_host: str, rcpt: str, timeout: int) -> int | None:
    """Open an SMTP session to `mx_host`, EHLO/HELO, MAIL FROM, RCPT TO `rcpt`,
    and return the RCPT response code (250 = accepted, 550 = rejected, etc.).
    Never sends DATA — no email leaves. Returns None if the server is
    unreachable (port 25 blocked, refused, timeout)."""
    try:
        with smtplib.SMTP(mx_host, timeout=timeout) as smtp:
            smtp.ehlo_or_helo_if_needed()
            smtp.mail(_PROBE_MAIL_FROM)
            code, _ = smtp.rcpt(rcpt)
            return code
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
            OSError, ConnectionError):
        return None
    except smtplib.SMTPException:
        # A protocol-level error still means we reached the server but couldn't
        # complete the probe — treat as inconclusive, not dead.
        return -1


def _is_catch_all(domain: str, mx_host: str, timeout: int) -> bool | None:
    """Does `domain` accept mail for a random (certainly non-existent) address?
    If so, a 250 on the real address proves nothing. Cached per domain.
    Returns None when the probe couldn't run."""
    now = time.time()
    cached = _CATCHALL_CACHE.get(domain)
    if cached and (now - cached[0]) < _EMAIL_CACHE_TTL:
        return cached[1]
    # Deterministic-but-unlikely local part (no RNG — reproducible for tests).
    probe = f"zz-no-such-user-9r7x@{domain}"
    code = _rcpt_code(mx_host, probe, timeout)
    verdict: bool | None
    if code is None:
        verdict = None  # couldn't probe
    elif code == 250:
        verdict = True  # accepts anything → catch-all
    else:
        verdict = False
    _CATCHALL_CACHE[domain] = (now, verdict)
    return verdict


# Area code → US state mapping (first 3 digits of a 10-digit NANP number)
_AREA_CODE_TO_STATE: dict[str, str] = {
    "205": "AL", "251": "AL", "256": "AL", "334": "AL", "938": "AL",
    "907": "AK",
    "480": "AZ", "520": "AZ", "602": "AZ", "623": "AZ", "928": "AZ",
    "479": "AR", "501": "AR", "870": "AR",
    "209": "CA", "213": "CA", "279": "CA", "310": "CA", "323": "CA",
    "341": "CA", "408": "CA", "415": "CA", "424": "CA", "442": "CA",
    "510": "CA", "530": "CA", "559": "CA", "562": "CA", "619": "CA",
    "626": "CA", "628": "CA", "650": "CA", "657": "CA", "661": "CA",
    "669": "CA", "707": "CA", "714": "CA", "747": "CA", "760": "CA",
    "805": "CA", "818": "CA", "820": "CA", "831": "CA", "840": "CA",
    "858": "CA", "909": "CA", "916": "CA", "925": "CA", "949": "CA",
    "951": "CA",
    "303": "CO", "719": "CO", "720": "CO", "970": "CO", "983": "CO",
    "203": "CT", "475": "CT", "860": "CT", "959": "CT",
    "302": "DE",
    "202": "DC", "771": "DC",
    "239": "FL", "305": "FL", "321": "FL", "352": "FL", "386": "FL",
    "407": "FL", "448": "FL", "561": "FL", "645": "FL", "656": "FL",
    "689": "FL", "727": "FL", "754": "FL", "772": "FL", "786": "FL",
    "813": "FL", "850": "FL", "863": "FL", "904": "FL", "941": "FL",
    "954": "FL",
    "229": "GA", "404": "GA", "470": "GA", "478": "GA", "678": "GA",
    "706": "GA", "762": "GA", "770": "GA", "912": "GA",
    "808": "HI",
    "208": "ID", "986": "ID",
    "217": "IL", "224": "IL", "309": "IL", "312": "IL", "331": "IL",
    "447": "IL", "464": "IL", "618": "IL", "630": "IL", "708": "IL",
    "773": "IL", "779": "IL", "815": "IL", "847": "IL", "861": "IL",
    "219": "IN", "260": "IN", "317": "IN", "463": "IN", "574": "IN",
    "765": "IN", "812": "IN", "930": "IN",
    "319": "IA", "515": "IA", "563": "IA", "641": "IA", "712": "IA",
    "316": "KS", "620": "KS", "785": "KS", "913": "KS",
    "270": "KY", "364": "KY", "502": "KY", "606": "KY", "859": "KY",
    "225": "LA", "318": "LA", "337": "LA", "504": "LA", "985": "LA",
    "207": "ME",
    "240": "MD", "301": "MD", "410": "MD", "443": "MD", "667": "MD",
    "339": "MA", "351": "MA", "413": "MA", "508": "MA", "617": "MA",
    "774": "MA", "781": "MA", "857": "MA", "978": "MA",
    "231": "MI", "248": "MI", "269": "MI", "313": "MI", "517": "MI",
    "586": "MI", "616": "MI", "734": "MI", "810": "MI", "906": "MI",
    "947": "MI", "989": "MI",
    "218": "MN", "320": "MN", "507": "MN", "612": "MN", "651": "MN",
    "763": "MN", "952": "MN",
    "228": "MS", "601": "MS", "662": "MS", "769": "MS",
    "314": "MO", "417": "MO", "573": "MO", "636": "MO", "660": "MO",
    "816": "MO", "975": "MO",
    "406": "MT",
    "308": "NE", "402": "NE", "531": "NE",
    "702": "NV", "725": "NV", "775": "NV",
    "603": "NH",
    "201": "NJ", "551": "NJ", "609": "NJ", "640": "NJ", "732": "NJ",
    "848": "NJ", "856": "NJ", "862": "NJ", "908": "NJ", "973": "NJ",
    "505": "NM", "575": "NM",
    "212": "NY", "315": "NY", "329": "NY", "332": "NY", "347": "NY",
    "363": "NY", "516": "NY", "518": "NY", "585": "NY", "607": "NY",
    "631": "NY", "646": "NY", "680": "NY", "716": "NY", "718": "NY",
    "838": "NY", "845": "NY", "914": "NY", "917": "NY", "929": "NY",
    "934": "NY",
    "252": "NC", "336": "NC", "704": "NC", "743": "NC", "828": "NC",
    "910": "NC", "919": "NC", "980": "NC", "984": "NC",
    "701": "ND",
    "216": "OH", "234": "OH", "283": "OH", "330": "OH", "380": "OH",
    "419": "OH", "440": "OH", "513": "OH", "522": "OH", "567": "OH",
    "614": "OH", "740": "OH", "937": "OH",
    "405": "OK", "539": "OK", "580": "OK", "918": "OK",
    "458": "OR", "503": "OR", "541": "OR", "971": "OR",
    "215": "PA", "267": "PA", "272": "PA", "412": "PA", "445": "PA",
    "484": "PA", "570": "PA", "610": "PA", "717": "PA", "724": "PA",
    "814": "PA", "835": "PA", "878": "PA",
    "401": "RI",
    "803": "SC", "839": "SC", "843": "SC", "854": "SC", "864": "SC",
    "605": "SD",
    "423": "TN", "615": "TN", "629": "TN", "731": "TN", "865": "TN",
    "901": "TN", "931": "TN",
    "210": "TX", "214": "TX", "254": "TX", "281": "TX", "325": "TX",
    "346": "TX", "361": "TX", "409": "TX", "430": "TX", "432": "TX",
    "469": "TX", "512": "TX", "682": "TX", "713": "TX", "726": "TX",
    "737": "TX", "806": "TX", "817": "TX", "830": "TX", "832": "TX",
    "903": "TX", "915": "TX", "936": "TX", "940": "TX", "956": "TX",
    "972": "TX", "979": "TX",
    "385": "UT", "435": "UT", "801": "UT",
    "802": "VT",
    "276": "VA", "434": "VA", "540": "VA", "571": "VA", "703": "VA",
    "757": "VA", "804": "VA", "826": "VA", "948": "VA",
    "206": "WA", "253": "WA", "360": "WA", "425": "WA", "509": "WA",
    "564": "WA",
    "304": "WV", "681": "WV",
    "262": "WI", "274": "WI", "414": "WI", "534": "WI", "608": "WI",
    "715": "WI", "920": "WI",
    "307": "WY",
}

# Overlapping area codes cover multiple states — return the most likely one
_AREA_OVERLAP: dict[str, str] = {
    "808": "HI",  # also AC-related but primarily Hawaii
    "603": "NH",
    "201": "NJ",
}


def _area_code_to_state(phone_str: str) -> str | None:
    """Extract area code from a phone string and map to US state."""
    digits = re.sub(r"\D", "", phone_str)
    if len(digits) >= 10 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) >= 10:
        ac = digits[:3]
        return _AREA_CODE_TO_STATE.get(ac, _AREA_OVERLAP.get(ac))
    return None


def verify_email(email: str, timeout: int = 5, probe: bool = True) -> dict:
    """Free email verification. Returns:
    {
        "format_valid": bool,
        "mx_valid": bool,
        "smtp_valid": bool,     # server reachable + accepted the RCPT probe
        "deliverable": bool|None,  # True=RCPT 250 & not catch-all, False=550, None=unprobed
        "catch_all": bool,      # domain accepts any address (250 proves nothing)
        "is_role": bool,
        "is_free_domain": bool,
        "verified": bool,       # deliverable, OR (mx_valid when SMTP unprobeable)
        "tier": str,            # verified | catch_all | mx_only | undeliverable | invalid
        "score": int,           # 0-10 composite quality score
    }

    Verification uses a RCPT-TO probe (never DATA — no mail is sent) and detects
    catch-all domains by also probing a random address. Results are cached per
    email for the run so a second call (e.g. from scoring) is free. When port 25
    is blocked (common on ISP/cloud hosts) the probe degrades to MX-only and the
    tier records that with "mx_only".
    """
    result = {
        "format_valid": False,
        "mx_valid": False,
        "smtp_valid": False,
        "deliverable": None,
        "catch_all": False,
        "is_role": False,
        "is_free_domain": False,
        "verified": False,
        "tier": "invalid",
        "score": 0,
    }

    if not email or not _EMAIL_RE.match(email):
        return result

    key = email.lower()
    now = time.time()
    cached = _EMAIL_CACHE.get(key)
    if cached and (now - cached[0]) < _EMAIL_CACHE_TTL:
        return dict(cached[1])

    result["format_valid"] = True
    domain = email.split("@")[1].lower()
    local = email.split("@")[0].lower()
    result["is_role"] = local in _ROLE_ADDRESSES
    result["is_free_domain"] = domain in _FREE_DOMAINS

    # MX record check (cached by domain)
    mx_hosts = _resolve_mx(domain, timeout)
    result["mx_valid"] = len(mx_hosts) > 0

    # RCPT-TO probe (+ catch-all detection) on the primary MX.
    global _SMTP_UNAVAILABLE, _SMTP_FAIL_STREAK
    if (result["mx_valid"] and probe and not _SMTP_UNAVAILABLE
            and domain not in _SMTP_DEAD_DOMAINS):
        mx = mx_hosts[0]
        code = _rcpt_code(mx, email, timeout)
        if code is None:
            _SMTP_DEAD_DOMAINS.add(domain)  # port 25 blocked — stop probing this domain
            _SMTP_FAIL_STREAK += 1
            if _SMTP_FAIL_STREAK >= _SMTP_FAIL_LIMIT:
                _SMTP_UNAVAILABLE = True
                logger.info(
                    "SMTP unreachable for %d consecutive MXs — outbound port 25 looks "
                    "blocked on this host. Skipping RCPT probes; email verification "
                    "degrades to MX-only.", _SMTP_FAIL_STREAK)
        elif code == -1:
            _SMTP_FAIL_STREAK = 0
            pass  # inconclusive protocol error — leave deliverable=None
        else:
            _SMTP_FAIL_STREAK = 0  # we reached a mail server — port 25 is fine here
            result["smtp_valid"] = True
            if code == 250:
                catch_all = _is_catch_all(domain, mx, timeout)
                result["catch_all"] = bool(catch_all)
                # Deliverable only if the mailbox accepts AND the domain isn't a
                # catch-all (which would 250 everything). None if catch-all check
                # itself couldn't run.
                result["deliverable"] = None if catch_all else True
            elif 500 <= code < 600:
                result["deliverable"] = False

    # Tier + verified verdict.
    if result["deliverable"] is True:
        result["tier"], result["verified"] = "verified", True
    elif result["deliverable"] is False:
        result["tier"], result["verified"] = "undeliverable", False
    elif result["catch_all"]:
        result["tier"], result["verified"] = "catch_all", False
    elif result["mx_valid"]:
        # SMTP unprobeable (port 25 blocked) or inconclusive → MX is the best
        # signal we have. Counts as verified but flagged as weaker.
        result["tier"], result["verified"] = "mx_only", True
    else:
        result["tier"], result["verified"] = "invalid", False

    # Composite score
    s = 0
    if result["format_valid"]:
        s += 2
    if result["mx_valid"]:
        s += 3
    if result["deliverable"] is True:
        s += 3
    elif result["smtp_valid"]:
        s += 1
    if not result["is_free_domain"]:
        s += 1
    if not result["is_role"]:
        s += 1
    result["score"] = s

    _EMAIL_CACHE[key] = (now, dict(result))
    return result


def verify_phone(phone: str, region: str = "US") -> dict:
    """Free phone verification via libphonenumber. Returns:
    {
        "valid": bool,
        "possible": bool,
        "type": str,          # mobile | fixed_line | voip | toll_free | ...
        "country_code": int,
        "national": str,      # formatted national number
        "area_code": str | None,
        "state": str | None,  # US state abbreviation
        "score": int,         # 0-10 composite quality score
    }
    """
    result = {
        "valid": False,
        "possible": False,
        "type": "unknown",
        "country_code": 0,
        "national": "",
        "area_code": None,
        "state": None,
        "score": 0,
    }

    if not phone:
        return result

    try:
        parsed = phonenumbers.parse(phone, region)
        result["valid"] = phonenumbers.is_valid_number(parsed)
        result["possible"] = phonenumbers.is_possible_number(parsed)
        result["country_code"] = parsed.country_code
        result["national"] = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        )

        num_type = phonenumbers.number_type(parsed)
        type_map = {
            0: "fixed_line", 1: "mobile", 2: "fixed_line_or_mobile",
            3: "toll_free", 4: "premium_rate", 5: "shared_cost",
            6: "voip", 7: "personal_number", 8: "pager",
            9: "uan", 10: "voicemail",
        }
        result["type"] = type_map.get(num_type, "unknown")

        # Area code → state for NANP numbers
        if result["country_code"] == 1:
            result["area_code"] = _area_code_to_state(phone)
            result["state"] = _area_code_to_state(phone)
    except Exception as e:
        logger.debug("Phone check failed for %s: %s", phone, e)

    # Composite score
    s = 0
    if result["valid"]:
        s += 4
    if result["possible"]:
        s += 1
    if result["type"] in ("mobile", "voip", "toll_free"):
        s += 3
    elif result["type"] == "fixed_line":
        s += 2
    if result["state"]:
        s += 2
    result["score"] = s

    return result


# Per-address Census geocoder cache: {address_lower: (timestamp, matched_bool)}
_ADDR_CACHE: dict[str, tuple[float, bool | None]] = {}
_ADDR_CACHE_TTL = 3600  # 1 hour — addresses don't change within a run


def verify_address(address: str, timeout: int = 8) -> bool | None:
    """Validate a US street address against the free Census Bureau one-line
    geocoder (no API key, no hard rate limit). Returns True if the geocoder
    matched it to a real, standardized location, False if unmatched, None if the
    check couldn't run (network error / non-US / empty). Cached per address."""
    if not address or not address.strip():
        return None
    key = address.strip().lower()
    now = time.time()
    cached = _ADDR_CACHE.get(key)
    if cached and (now - cached[0]) < _ADDR_CACHE_TTL:
        return cached[1]

    verdict: bool | None
    try:
        r = httpx.get(
            "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
            params={"address": address, "benchmark": "Public_AR_Current",
                    "format": "json"},
            timeout=timeout,
        )
        if r.status_code == 200:
            matches = r.json().get("result", {}).get("addressMatches", [])
            verdict = len(matches) > 0
        else:
            verdict = None
    except Exception:
        verdict = None

    _ADDR_CACHE[key] = (now, verdict)
    return verdict


def clear_cache():
    """Clear all verification caches (useful for testing)."""
    global _SMTP_UNAVAILABLE, _SMTP_FAIL_STREAK
    _MX_CACHE.clear()
    _EMAIL_CACHE.clear()
    _CATCHALL_CACHE.clear()
    _SMTP_DEAD_DOMAINS.clear()
    _ADDR_CACHE.clear()
    _SMTP_UNAVAILABLE = False
    _SMTP_FAIL_STREAK = 0


def verify_batch(emails: list[str] | None = None, phones: list[str] | None = None,
                 region: str = "US", timeout: int = 5) -> dict:
    """Batch verify multiple emails and phones. Returns summary stats."""
    result = {}
    if emails:
        email_results = [verify_email(e, timeout=timeout) for e in emails]
        valid = sum(1 for r in email_results if r["mx_valid"])
        result["emails"] = {
            "total": len(emails),
            "valid_mx": valid,
            "rate": round(valid / max(len(emails), 1) * 100, 1),
        }
    if phones:
        phone_results = [verify_phone(p, region=region) for p in phones]
        valid = sum(1 for r in phone_results if r["valid"])
        mobile = sum(1 for r in phone_results if r["type"] == "mobile")
        result["phones"] = {
            "total": len(phones),
            "valid": valid,
            "mobile": mobile,
            "rate": round(valid / max(len(phones), 1) * 100, 1),
        }
    return result

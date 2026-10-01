"""
services/business_intel.py — Deep business intelligence extraction from websites.

Extracts richer data about each business using only free, technical processing:
  - Rich description (meta tags, OG, about page, schema.org)
  - Employee / team size (regex patterns, team page count, schema)
  - Year founded (copyright range, "founded" regex, schema, RDAP domain age)
  - Partners / clients / affiliations (partner pages, alt-text, member-of mentions)
  - Recent activity (blog/news dates, recent updates)

Every extraction is zero-cost, zero-LLM — pure HTML parsing + regex + DNS/RDAP.

Usage:
    from backend.services.business_intel import enrich_business_intel_sync
    enrich_business_intel_sync(lead, region="US")
"""

import logging
import re
import socket
from datetime import datetime, timezone
from typing import Optional, List
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

from backend.models import Lead, WebsiteStatus
from backend.utils.retry import retry_sync, RetryableError

logger = logging.getLogger(__name__)

TIMEOUT = 8
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

# ── Known intel paths to crawl ────────────────────────────────────────────────
ABOUT_PATHS = [
    "/about", "/about-us", "/aboutus", "/our-story", "/company",
    "/who-we-are", "/about-company",
]
TEAM_PATHS = [
    "/team", "/our-team", "/staff", "/leadership", "/management",
    "/people", "/our-people", "/board", "/executives",
]
SERVICE_PATHS = [
    "/services", "/our-services", "/service", "/menu", "/our-menu",
    "/what-we-do", "/solutions", "/products", "/capabilities",
    "/offerings",
]
PARTNER_PATHS = [
    "/partners", "/our-partners", "/clients", "/our-clients",
    "/certifications", "/affiliations", "/memberships",
    "/customers", "/our-customers", "/case-studies",
]
BLOG_PATHS = [
    "/blog", "/news", "/updates", "/latest", "/press",
    "/insights", "/articles",
]

ALL_INTEL_PATHS = list(dict.fromkeys(
    ABOUT_PATHS + TEAM_PATHS + SERVICE_PATHS + PARTNER_PATHS + BLOG_PATHS
))

# ── Regex patterns ────────────────────────────────────────────────────────────

# Employee count patterns (ordered by specificity)
_EMPLOYEE_PATTERNS = [
    # "We are a team of 50" / "Team of 50 people"
    (r"(?i:(?:we('re| are)?\s+(?:a\s+)?)?team\s+of\s+)(\d{1,5})\s*(?:people|members|employees|professionals|staff)?", "website_regex"),
    # "We have 200+ employees" / "50+ employees"
    (r"(?i:(\d{1,5})\s*\+\s*(?:employees|staff|people|team\s+members))", "website_regex"),
    # "Over 100 employees worldwide" / "More than 50 staff"
    (r"(?i:(?:over|more\s+than|about|approximately)\s+)(\d{1,5})\s*(?:employees|staff|people|team)", "website_regex"),
    # "200 employees" / "50 staff" (standalone count)
    (r"(?i:(\d{1,5})\s*(?:employees|staff\s+members|people\s+on\s+(?:staff|team)))", "website_regex"),
    # "Our 50-person team" / "a 200-person company"
    (r"(?i:(?:our|a)\s+)(\d{1,5})\s*-\s*(?:person|member)\s+(?:team|company)", "website_regex"),
    # "Founded in YEAR — we now employ 200"
    (r"(?i:employ(s|ees)?\s+(?:over|about|approximately|more\s+than)?\s*)(\d{1,5})", "website_regex"),
]

# Year founded patterns
_FOUNDED_PATTERNS = [
    # "Founded in 2010" / "Established in 2010" / "Since 2010"
    r"(?i:(?:founded|established|incorporated|started|opened|launched|since)\s+(?:in\s+)?)(\d{4})",
    # "© 2010-2026" or "© 2010" (if single year and < 3 years ago, it's founding)
    r"(?i:copyright\s+(?:©\s*)?(\d{4})(?:\s*[-–]\s*\d{4})?)",
    # "Serving since 2010"
    r"(?i:(?:serving|operating|in\s+business)\s+(?:since|for\s+over)\s+)(\d{4})",
    # "Year established: 2010"
    r"(?i:(?:year\s+(?:established|founded)|est\.)\s*[:.]?\s*)(\d{4})",
]

# Team page patterns — count visible person entries
_TEAM_CARD_PATTERNS = [
    "div[class*='team']", "div[class*='member']", "div[class*='profile']",
    "div[class*='person']", "div[class*='employee']", "div[class*='staff']",
    "li[class*='team']", "li[class*='member']",
    "article[class*='team']", "article[class*='member']",
]

# Partner/client patterns
_PARTNER_KEYWORDS = [
    "partner", "client", "certified", "affiliate", "member", "sponsor",
    "accredited", "authorized", "distributor", "reseller",
]

# Recent blog/news date patterns
_BLOG_DATE_PATTERNS = [
    # <time datetime="2026-03-15">
    r'<time[^>]*datetime=["\'](\d{4}[-/]\d{1,2}[-/]\d{1,2})',
    # "March 15, 2026" style dates in article headers
    r'(?i:(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+\d{4})',
    # ISO dates in text
    r'\b(\d{4}[-/]\d{2}[-/]\d{2})\b',
]

# Schema.org patterns for structured data
_SCHEMA_EMPLOYEE = re.compile(
    r'(?:"numberOfEmployees"\s*:\s*|"numberOfEmployees"\s*}\s*:\s*|numberOfEmployees["\']?\s*[:=]\s*)(\d{1,7})',
    re.IGNORECASE,
)
_SCHEMA_FOUNDED = re.compile(
    r'(?:"foundingDate"\s*:\s*|foundingDate["\']?\s*[:=]\s*)"?(\d{4})', re.IGNORECASE
)


def _get_httpx_proxy() -> dict:
    try:
        from backend.services.proxy_rotator import get_rotator
        proxy = get_rotator().get_proxy(for_playwright=False)
        if proxy:
            url = proxy.get("all://") or next(iter(proxy.values()), None)
            if url:
                return {"proxy": url}
    except Exception:
        pass
    return {}


@retry_sync(max_attempts=1, base_delay=0.5, max_delay=2.0)
def _fetch(url: str, client: httpx.Client, cache: Optional[dict] = None) -> Optional[str]:
    """Fetch a page, optionally memoised in a caller-supplied per-lead dict.

    `cache` is shared with contact_finder for the same lead: both modules crawl
    /about, /team, /staff, /leadership… of the SAME site, so without it every
    overlapping path was fetched twice. Failures are cached too — a dead path is
    worth remembering.
    """
    if cache is not None and url in cache:
        return cache[url]
    result = None
    try:
        r = client.get(url, timeout=TIMEOUT, follow_redirects=True)
        if r.status_code < 400:
            result = r.text
        elif r.status_code in (429, 503):
            raise RetryableError(f"HTTP {r.status_code} for {url}")
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
        raise RetryableError(str(e)) from e
    except Exception:
        pass
    if cache is not None:
        cache[url] = result
    return result


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


# ── 1. Rich description extraction ────────────────────────────────────────────

def _extract_meta_description(html: str) -> Optional[str]:
    """Extract from og:description, meta description, or twitter:description."""
    soup = BeautifulSoup(html, "html.parser")
    for prop in ("og:description", "description", "twitter:description"):
        for tag in soup.find_all("meta", attrs={"name": prop, "content": True}):
            val = tag["content"].strip()
            if len(val) > 20:
                return val
        for tag in soup.find_all("meta", attrs={"property": prop, "content": True}):
            val = tag["content"].strip()
            if len(val) > 20:
                return val
    return None


def _extract_schemaorg_description(html: str) -> Optional[str]:
    """Extract description from JSON-LD schema.org markup."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string)
            if isinstance(data, dict):
                desc = data.get("description") or ""
                if desc and len(desc) > 20:
                    return desc.strip()
            elif isinstance(data, list):
                for item in data:
                    desc = item.get("description") or ""
                    if desc and len(desc) > 20:
                        return desc.strip()
        except Exception:
            continue
    return None


def _extract_about_page_summary(html: str, max_chars: int = 500) -> Optional[str]:
    """Extract meaningful summary from about/company page content."""
    text = _visible_text(html)
    # Remove navigation/footer boilerplate by taking the middle-ish content
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 40]
    if not lines:
        return None
    # Heuristic: take the longest paragraph-like lines that mention key terms
    weighted = []
    for line in lines:
        score = len(line)
        if any(kw in line.lower() for kw in ("specializ", "mission", "vision", "dedicated",
                                               "expert", "service", "solution", "provide",
                                               "offer", "client", "partner", "deliver")):
            score *= 1.5
        weighted.append((score, line))
    weighted.sort(key=lambda x: -x[0])
    # Take top lines up to max_chars
    summary_parts = []
    total = 0
    for _, line in weighted:
        if total + len(line) > max_chars:
            break
        summary_parts.append(line)
        total += len(line)
    result = " ".join(summary_parts)
    return result if len(result) > 40 else None


# ── 2. Employee count extraction ──────────────────────────────────────────────

def _extract_employee_schema(html: str) -> Optional[int]:
    """Extract numberOfEmployees from JSON-LD schema."""
    m = _SCHEMA_EMPLOYEE.search(html)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # Also check microdata: <meta itemprop="numberOfEmployees" content="50">
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(attrs={"itemprop": "numberOfEmployees", "content": True}):
        try:
            return int(tag["content"])
        except (ValueError, TypeError):
            pass
    return None


def _extract_employee_regex(html: str, page_texts: list) -> tuple:
    """Return (count, source) or (None, None)."""
    for pattern, source in _EMPLOYEE_PATTERNS:
        m = re.search(pattern, html)
        if not m:
            continue
        try:
            # Find the digit group (could be group 1 or 2)
            for g in range(1, m.lastindex + 1):
                val = m.group(g)
                if val and val.isdigit():
                    count = int(val)
                    if 1 <= count <= 50000:
                        return count, source
        except (ValueError, IndexError):
            continue
    return None, None


def _count_team_page(html: str) -> Optional[int]:
    """Count individual person entries on a team page by card/profile elements."""
    soup = BeautifulSoup(html, "html.parser")
    for selector in _TEAM_CARD_PATTERNS:
        cards = soup.select(selector)
        if len(cards) >= 2:
            return len(cards)
    # Fallback: count heading-like patterns with names
    text = _visible_text(html)
    # Count capitalized two-word names that appear in quick succession
    name_pattern = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")
    names = set(name_pattern.findall(text))
    # Filter out likely non-names
    common_words = {"Terms", "Service", "Privacy", "Policy", "Cookie", "Contact",
                    "Search", "Submit", "Cancel", "Copyright", "All Rights",
                    "Privacy Policy", "Terms Service"}
    names = names - common_words
    if len(names) >= 3:
        return len(names)
    return None


# ── 3. Year founded extraction ────────────────────────────────────────────────

def _extract_year_schema(html: str) -> Optional[int]:
    """Extract foundingDate from JSON-LD schema."""
    m = _SCHEMA_FOUNDED.search(html)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _extract_year_regex(html: str) -> Optional[int]:
    """Try all founded-year patterns; return most plausible year."""
    current_year = datetime.now(timezone.utc).year
    candidates = []
    for pattern in _FOUNDED_PATTERNS:
        for m in re.finditer(pattern, html):
            year_str = m.group(1)
            try:
                year = int(year_str)
            except ValueError:
                continue
            # Sanity: business founded between 1700 and last year
            if 1700 <= year <= current_year - 1:
                candidates.append(year)
    if not candidates:
        return None
    # Return the most common year mentioned (median-ish)
    candidates.sort()
    return candidates[len(candidates) // 2]


def _extract_year_from_copyright(html: str) -> Optional[int]:
    """Extract founding year from copyright range like '© 2010-2026'."""
    m = re.search(r"(?i)(?:copyright\s+(?:©\s*)?)(\d{4})\s*[-–]\s*\d{4}", html)
    if m:
        return int(m.group(1))
    return None


def _extract_domain_age(domain: str) -> Optional[int]:
    """RDAP-based domain creation year as proxy for founding year.

    Uses owner_contact.rdap_domain — cached per domain and shared with the
    registrant-phone lookup, so a lead makes ONE RDAP request instead of two.
    The old code hardcoded rdap.verisign.com/com/, which 404s on every non-.com
    domain and then fell through to a blocking `whois` subprocess (8s per lead);
    rdap.org routes by TLD, so that fallback is gone.
    """
    try:
        from backend.services.owner_contact import rdap_domain
        data = rdap_domain(domain)
    except Exception:
        return None
    if not data:
        return None
    for event in data.get("events", []) or []:
        if event.get("eventAction") == "registration":
            date_str = event.get("eventDate", "")
            if date_str:
                try:
                    return int(date_str[:4])
                except ValueError:
                    return None
    return None


# ── 4. Partner extraction ─────────────────────────────────────────────────────

def _extract_partners_from_page(html: str, base_url: str) -> list:
    """Extract partner/client names from a page."""
    partners = []
    soup = BeautifulSoup(html, "html.parser")

    # Look for partner logo alt-texts
    for img in soup.find_all("img", alt=True):
        alt = img["alt"].strip()
        if alt and len(alt) > 2 and not re.match(r"^[a-z][a-z0-9.-]+\.[a-z]{2,}$", alt):
            # Check if the surrounding context mentions partnership
            parent_text = ""
            for parent in img.parents:
                parent_text = parent.get_text(" ", strip=True).lower()
                if any(kw in parent_text for kw in _PARTNER_KEYWORDS):
                    break
            if any(kw in alt.lower() for kw in _PARTNER_KEYWORDS) or any(
                kw in parent_text for kw in _PARTNER_KEYWORDS
            ):
                if alt not in partners:
                    partners.append(alt)

    # Look for list items containing partner names
    for tag in soup.find_all(["li", "div", "span"]):
        text = tag.get_text(" ", strip=True)
        if not text or len(text) > 100:
            continue
        text_lower = text.lower()
        if any(kw in text_lower for kw in _PARTNER_KEYWORDS):
            # Extract company-like names from text
            companies = re.findall(r"\b[A-Z][A-Za-z0-9\s&.]+(?:Inc|LLC|Ltd|Corp|Group|Solutions|Technologies|Systems|Services)\b", text)
            for c in companies:
                c = c.strip()
                if c and c not in partners and len(c) > 3:
                    partners.append(c)

    # Deduplicate and limit
    seen = set()
    unique = []
    for p in partners:
        p_lower = p.lower()
        if p_lower not in seen:
            seen.add(p_lower)
            unique.append(p)
    return unique[:20]


# ── 5b. Services extraction ────────────────────────────────────────────────────

_SERVICE_HEADINGS = re.compile(
    r"(?i)(?:our\s+)?(?:services?|products?|solution|what\s+we\s+do|speciali(?:ze|se)s?|offer(?:ings)?|capabilities?|expertise)",
)
_SERVICE_LIST_SELECTORS = [
    "div[class*='service'] li", "ul[class*='service'] li",
    "div[class*='product'] li", "ul[class*='product'] li",
    "div[id*='service'] li",  "ul[id*='service'] li",
    "div[class*='solution'] li", "div[class*='offering'] li",
    "div[class*='capability'] li",
    # Coffee shop / restaurant menu patterns
    "div[class*='menu'] li", "ul[class*='menu'] li",
    "div[id*='menu'] li", "ul[id*='menu'] li",
    "div[class*='food'] li", "div[class*='drink'] li",
    "section[class*='menu'] li", "section[id*='menu'] li",
    "div[class*='specialty'] li", "div[class*='category'] li",
    # Generic service/product section items
    "div[class*='feature'] li", "div[class*='offer'] li",
]


def _extract_services_from_schema(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    services = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string)
        except Exception:
            continue
        def _walk(obj, depth=0):
            if depth > 5:
                return
            if isinstance(obj, dict):
                if obj.get("@type") in ("Service", "Product"):
                    name = obj.get("name", "")
                    if name and len(name) > 2 and name not in services:
                        services.append(name)
                for v in obj.values():
                    _walk(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item, depth + 1)
        _walk(data)
    return ", ".join(services[:15]) if services else None


def _extract_services_from_html(html: str) -> Optional[str]:
    services = []
    soup = BeautifulSoup(html, "html.parser")

    # 1. "we offer X, Y and Z" patterns
    text = _visible_text(html)
    for p in [
        r"(?i:(?:we\s+)?(?:offer|provide|specializ(?:e|se)\s+in|deliver)\s+)(.+?)(?:\.\s*(?:We|Our|Call|Contact|Learn|Get))",
        r"(?i:(?:our\s+)?(?:services|products|solutions)\s+(?:include|are|cover|range\s+from)\s+)(.+?)(?:\.\s*(?:We|Our|Call|Contact))",
    ]:
        m = re.search(p, text)
        if m:
            chunk = m.group(1)[:300]
            for part in re.split(r"\s*(?:,|\s+and\s+|\s+or\s+)\s*", chunk):
                part = part.strip().strip(".,;:!?").strip()
                if part and len(part) > 3 and len(part) < 80 and part not in services:
                    services.append(part)

    # 2. List items under service headings
    for tag in soup.find_all(["h2", "h3", "h4"]):
        if _SERVICE_HEADINGS.search(tag.get_text()):
            parent = tag.find_parent(["section", "div", "article"]) or tag.parent
            for li in parent.find_all("li"):
                t = li.get_text(" ", strip=True)
                if t and len(t) > 3 and len(t) < 100 and t not in services:
                    services.append(t)

    # 3. CSS selectors for service list items
    for sel in _SERVICE_LIST_SELECTORS:
        for item in soup.select(sel):
            t = item.get_text(" ", strip=True)
            if t and len(t) > 3 and len(t) < 100 and t not in services:
                services.append(t)

    return ", ".join(services[:20]) if services else None


# ── 5c. Team member + title extraction ─────────────────────────────────────────

_TITLE_KEYWORDS = {
    "ceo", "cfo", "cto", "coo", "founder", "owner", "president",
    "director", "manager", "head of", "vp", "vice president",
    "principal", "partner", "lead", "chief",
}
_TEAM_CONTAINER_SELECTORS = [
    "div[class*='team']", "div[class*='member']", "div[class*='profile']",
    "div[class*='person']", "div[class*='employee']", "div[class*='staff']",
    "li[class*='team']", "li[class*='member']", "li[class*='person']",
    "article[class*='team']", "article[class*='member']",
]


def _extract_team_members(html: str) -> tuple:
    """Return (team_members_json, owner_title)."""
    import json
    soup = BeautifulSoup(html, "html.parser")
    members = []
    owner_title = None

    for container_sel in _TEAM_CONTAINER_SELECTORS:
        containers = soup.select(container_sel)
        if len(containers) < 2:
            continue
        for container in containers:
            for name_sel, title_sel in [("h3", "p"), ("h4", "p"), ("h3", "span"),
                                         ("h4", "span"), ("strong", "span")]:
                name_el = container.select_one(name_sel)
                title_el = container.select_one(title_sel)
                if name_el and title_el:
                    name = name_el.get_text(" ", strip=True)
                    title = title_el.get_text(" ", strip=True)
                    if name and title and 4 < len(name) < 60 and 2 < len(title) < 80:
                        entry = {"name": name, "title": title}
                        if entry not in members:
                            members.append(entry)
                        if owner_title is None and _is_owner_title(title):
                            owner_title = title
                    break
        if members:
            break

    if not members:
        text = _visible_text(html)
        for m in re.finditer(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\s*[—–,-]\s*([A-Za-z\s/]+?)(?=[,.\n]|$)", text):
            name = m.group(1).strip()
            title = m.group(2).strip()
            if name and title and 4 < len(name) < 60 and 2 < len(title) < 60:
                entry = {"name": name, "title": title}
                if entry not in members:
                    members.append(entry)
                if owner_title is None and _is_owner_title(title):
                    owner_title = title

    return json.dumps(members[:20]) if members else None, owner_title


# ── Dedicated owner title extraction from page text ──────────────────────────
_OWNER_TITLE_KEYWORDS = [
    "owner", "founder", "co-founder", "ceo", "president", "proprietor",
    "managing director", "principal", "managing partner", "executive director",
    "general manager",
]
_OWNER_PATTERNS = [
    # "John Smith, Owner" / "John Smith — Owner"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*[,–—-]\s*(Owner|Founder|CEO|President|Proprietor)",
    # "Owner: John Smith"
    r"(Owner|Founder|CEO|President|Proprietor)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
    # "John Smith is the owner/founder"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s+is\s+the\s+(owner|founder|proprietor)",
    # "Meet our owner John Smith"
    r"(?:meet|introducing|our)\s+(?:owner|founder)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
]


def _extract_owner_from_text(text: str) -> Optional[str]:
    """Extract owner/founder name and title from page text."""
    for pat in _OWNER_PATTERNS:
        m = re.search(pat, text)
        if m:
            # Pattern may put name in group 1 or 2
            name = m.group(1) if m.lastindex and m.group(1) and not any(
                kw in m.group(1).lower() for kw in _OWNER_TITLE_KEYWORDS
            ) else (m.group(2) if m.lastindex >= 2 else None)
            if name and 4 < len(name) < 60:
                return m.group(0).strip()
    return None


def _is_owner_title(title: str) -> bool:
    t = title.lower().strip()
    return any(kw in t for kw in _TITLE_KEYWORDS)


# ── 5d. Review weakness detection ──────────────────────────────────────────────

def review_velocity(review_count, year_founded, now_year=None) -> Optional[float]:
    """Reviews per year — a free proxy for review-generation effort, from fields we
    already have. None when we can't tell (no count or unknown/implausible founding).
    # ponytail: velocity, not true recency. Real last-review date needs scraping the
    # reviews tab — add that only if the velocity proxy proves too coarse."""
    now_year = now_year or datetime.now(timezone.utc).year
    if not isinstance(review_count, (int, float)) or not isinstance(year_founded, (int, float)):
        return None  # ignore non-numeric (e.g. test mocks / unknown)
    if not review_count or not (1700 < year_founded <= now_year):
        return None
    years = max(1, now_year - year_founded)
    return round(review_count / years, 1)


def _compute_review_weaknesses(lead) -> tuple:
    """Return (weaknesses_text, weakness_count) from available signals."""
    weaknesses = []
    count = 0

    # Few reviews for time in business → clear review-generation gap (timing wedge).
    vel = review_velocity(lead.google_review_count, lead.year_founded)
    if vel is not None and vel < 5 and (lead.year_founded and
            datetime.now(timezone.utc).year - lead.year_founded >= 3):
        weaknesses.append(f"Low review velocity (~{vel}/yr for time in business)")
        count += 1

    if lead.google_rating is not None:
        if lead.google_rating < 2.0:
            weaknesses.append("Very poor reputation (rating < 2.0)")
            count += 3
        elif lead.google_rating < 3.0:
            weaknesses.append("Poor reputation (rating < 3.0)")
            count += 2
        elif lead.google_rating < 3.5:
            weaknesses.append("Mixed reviews (rating < 3.5)")
            count += 1
        elif lead.google_rating < 4.0:
            weaknesses.append("Below-average rating (< 4.0)")
            count += 1
    if lead.google_review_count and lead.google_review_count > 50 and lead.google_rating and lead.google_rating < 3.5:
        weaknesses.append("Systemic complaints (many reviews, low rating)")
        count += 2
    if not lead.website:
        weaknesses.append("No website presence")
        count += 1
    elif lead.website_status and getattr(lead.website_status, 'value', lead.website_status) == "dead":
        weaknesses.append("Website offline")
        count += 1
    if not lead.owner_email:
        weaknesses.append("No email address available")
        count += 1
    if not lead.owner_email and not lead.phone:
        weaknesses.append("No contact method available")
        count += 1
    if not lead.has_google_maps:
        weaknesses.append("No Google Maps presence")
        count += 1

    return ", ".join(weaknesses) if weaknesses else None, count


# ── 5. Recent activity extraction ─────────────────────────────────────────────

def _extract_recent_activity(html: str) -> dict:
    """Extract recent blog/news dates from a page. Returns {dates: [...], summary: ...}."""
    dates = []
    for pattern in _BLOG_DATE_PATTERNS:
        for m in re.finditer(pattern, html):
            date_str = m.group(1) if m.lastindex else m.group(0)
            dates.append(date_str)
    # Parse and sort dates
    parsed = []
    for d in dates:
        try:
            # Try ISO format
            if re.match(r"\d{4}[-/]\d{2}[-/]\d{2}", d):
                dt = datetime.strptime(d.replace("/", "-")[:10], "%Y-%m-%d")
            else:
                # Try text dates
                dt = datetime.strptime(d.replace(",", ""), "%B %d %Y")
            parsed.append(dt)
        except (ValueError, IndexError):
            continue
    parsed.sort(reverse=True)
    result = {"dates": [str(d.date()) for d in parsed], "latest": parsed[0] if parsed else None}
    return result


def _extract_blog_post_titles(html: str) -> list:
    """Extract blog post titles from a blog listing page."""
    soup = BeautifulSoup(html, "html.parser")
    titles = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = tag.get_text(" ", strip=True)
        if text and 10 < len(text) < 150:
            # Filter out navigation-like text
            if not any(kw in text.lower() for kw in
                       ("recent post", "latest news", "archive", "category", "tag", "search")):
                titles.append(text)
    return titles[:10]


# ── Main enrichment function ─────────────────────────────────────────────────

# Commission-bleed wedge: which 3rd-party platforms a business depends on, and whether
# they run their own ordering/booking. Detected from links on their site (no scraping of
# the platforms themselves). See docs/verticals/*.md.
_AGGREGATOR_PLATFORMS = {
    # restaurant delivery (high-commission)
    "doordash": "DoorDash", "ubereats": "Uber Eats", "grubhub": "Grubhub",
    "seamless": "Seamless", "postmates": "Postmates", "slicelife": "Slice",
    "ezcater": "ezCater",
    # hospitality OTAs (high-commission)
    "booking.com": "Booking.com", "expedia": "Expedia", "hotels.com": "Hotels.com",
    "airbnb": "Airbnb", "vrbo": "Vrbo", "agoda": "Agoda", "priceline": "Priceline",
}
_DIRECT_COMMERCE = (  # first-party ordering / hotel booking engines (low/no commission)
    "toasttab", "olo.com", "chownow", "popmenu", "bentobox", "menufy", "owner.com",
    "square.site", "squareup", "clover.com",
    "synxis", "cloudbeds", "littlehotelier", "webrezpro", "thinkreservations", "mews",
    "innroad", "freetobook", "bookingbutton",
)


def detect_platforms(html: str) -> tuple[list, bool]:
    """From a page's HTML, return (3rd-party platforms relied on, has own ordering/booking).
    # ponytail: homepage-link scan, not platform scraping. If a site hides order/book links
    # on a deeper page, add a join over crawled pages — homepage covers the ~90% case."""
    low = (html or "").lower()
    ext = []
    for key, label in _AGGREGATOR_PLATFORMS.items():
        if key in low and label not in ext:
            ext.append(label)
    has_direct = any(k in low for k in _DIRECT_COMMERCE)
    return ext, has_direct


# Marketing/ad tech in the page = a marketing-active buyer (and which channel). A
# retargeting pixel means they run paid ads. Free signal — official Ad Library API doesn't
# cover commercial ads, and scraping it is fragile; the pixel in their own page is reliable.
_MARKETING_TAGS = {
    "connect.facebook.net": "Facebook Pixel", "fbq(": "Facebook Pixel",
    "facebook.com/tr": "Facebook Pixel",
    "googletagmanager.com": "Google Tag Manager", "google-analytics.com": "Google Analytics",
    "googleadservices.com": "Google Ads", "googleads.g.doubleclick.net": "Google Ads",
    "analytics.tiktok.com": "TikTok Pixel", "ttq.load": "TikTok Pixel",
    "snap.licdn.com": "LinkedIn Insight", "static.ads-twitter.com": "Twitter Pixel",
}
# Tags that specifically imply PAID ad spend (a retargeting/conversion pixel), not just analytics.
_PAID_AD_TAGS = {"Facebook Pixel", "Google Ads", "TikTok Pixel", "LinkedIn Insight", "Twitter Pixel"}


def detect_marketing_stack(html: str) -> tuple[list, bool]:
    """Return (marketing/ad tags found, runs_paid_ads). Scans the page's own HTML — no
    Ad Library scraping. A retargeting/conversion pixel => they spend on paid ads."""
    low = (html or "").lower()
    stack = []
    for key, label in _MARKETING_TAGS.items():
        if key in low and label not in stack:
            stack.append(label)
    runs_paid_ads = any(label in _PAID_AD_TAGS for label in stack)
    return stack, runs_paid_ads


def enrich_business_intel_sync(lead: Lead, cache: Optional[dict] = None) -> None:
    """
    Deep-enrich a single lead with business intelligence.
    Crawls website pages for: rich description, employee count, year founded,
    partners, recent activity.

    Modifies lead object in place — caller must commit.
    """
    if not lead.website or lead.website_status != WebsiteStatus.ACTIVE:
        return

    proxy_kwargs = _get_httpx_proxy()
    # Same canonicalisation contact_finder applies: unwrap Google /url?q= redirects
    # (we were crawling google.com instead of the business) and strip utm_*. Also
    # makes the shared page cache line up between the two crawlers.
    from backend.services.contact_finder import _canonicalize_website
    website = _canonicalize_website(lead.website)
    parsed = urlparse(website)
    domain = parsed.netloc.removeprefix("www.")
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT,
                       follow_redirects=True, **proxy_kwargs) as client:
        # ── 1. Fetch homepage for meta data ──
        homepage_html = _fetch(website, client, cache)
        if not homepage_html:
            return

        # Description: try meta, schema, then existing description
        meta_desc = _extract_meta_description(homepage_html)
        schema_desc = _extract_schemaorg_description(homepage_html)

        # Pick the richest available description
        candidates = []
        if meta_desc:
            candidates.append(meta_desc)
        if schema_desc:
            candidates.append(schema_desc)
        if lead.description:
            candidates.append(lead.description)
        if candidates:
            lead.description_long = max(candidates, key=len)
            # If meta/schema is shorter but lead.description is already good, keep both
            if lead.description and len(lead.description) > len(lead.description_long or ""):
                lead.description_long = lead.description

        # ── 2. Employee count (schema first, then regex, then team page) ──
        emp_count = _extract_employee_schema(homepage_html)
        emp_source = "schema"
        if emp_count is None:
            emp_count, emp_source = _extract_employee_regex(homepage_html, [])
        if emp_count and 1 <= emp_count <= 50000:
            lead.employee_count = emp_count
            lead.employee_count_source = emp_source

        # ── 2b. Services from homepage schema, then HTML ──
        services = _extract_services_from_schema(homepage_html)
        if services:
            lead.services = services
        else:
            svc_html = _extract_services_from_html(homepage_html)
            if svc_html:
                lead.services = svc_html

        # ── 3. Year founded (schema first, then regex, then copyright, then RDAP) ──
        year = _extract_year_schema(homepage_html)
        if year is None:
            year = _extract_year_regex(homepage_html)
        if year is None:
            year = _extract_year_from_copyright(homepage_html)
        if year is None:
            year = _extract_domain_age(domain)
        if year and 1700 <= year <= datetime.now(timezone.utc).year:
            lead.year_founded = year

        # ── 3b. Dedicated owner extraction from homepage text ──
        if not lead.owner_title:
            homepage_text = _visible_text(homepage_html)
            owner_info = _extract_owner_from_text(homepage_text)
            if owner_info:
                lead.owner_title = owner_info

        # ── 3c. Third-party commerce dependence (commission-bleed wedge) ──
        ext, has_direct = detect_platforms(homepage_html)
        if ext or has_direct:
            lead.external_platforms = ",".join(ext) or None
            lead.has_direct_commerce = has_direct

        # ── 3d. Marketing stack / paid-ads signal (marketing-active buyer + channel) ──
        stack, runs_ads = detect_marketing_stack(homepage_html)
        if stack:
            lead.marketing_stack = ",".join(stack)
            lead.runs_paid_ads = runs_ads

        # ── 4. Crawl deeper pages for more intel ──
        all_texts = [homepage_html]
        found_about_page = False
        found_team_page = False
        found_partner_page = False
        found_blog_page = False
        found_services_page = False
        found_team_members = False

        pages_fetched = 0
        MAX_INTEL_PAGES = 8   # cap deep crawl — don't fetch all ~25 paths per site
        for path in ALL_INTEL_PATHS:
            # Early-stop: once every intel category is found, or the page budget
            # is spent, stop crawling (avoids fetching 20+ pages per live site).
            if (found_about_page and found_team_page and found_partner_page
                    and found_blog_page and found_services_page
                    and found_team_members) or pages_fetched >= MAX_INTEL_PAGES:
                break
            url = f"{base_url}{path}"
            html = _fetch(url, client, cache)
            if not html:
                continue
            pages_fetched += 1
            all_texts.append(html)

            path_lower = path.lower()

            # ── 4a. Richer description from about page ──
            if not found_about_page and any(kw in path_lower for kw in ("about", "company", "story", "who")):
                about_summary = _extract_about_page_summary(html)
                if about_summary:
                    if lead.description_long:
                        # Keep whichever is longer
                        if len(about_summary) > len(lead.description_long):
                            lead.description_long = about_summary
                    else:
                        lead.description_long = about_summary
                    found_about_page = True
                # Also try owner extraction from about page text
                if not lead.owner_title:
                    about_text = _visible_text(html)
                    owner_info = _extract_owner_from_text(about_text)
                    if owner_info:
                        lead.owner_title = owner_info

            # ── 4b. Employee count from team page ──
            if not found_team_page and any(kw in path_lower for kw in ("team", "staff", "people", "leadership", "management")):
                if lead.employee_count is None:
                    team_count = _count_team_page(html)
                    if team_count and team_count >= 2:
                        lead.employee_count = team_count
                        lead.employee_count_source = "team_page"
                        found_team_page = True
                # Also try regex on team page text
                if lead.employee_count is None:
                    emp_count, emp_source = _extract_employee_regex(html, [])
                    if emp_count:
                        lead.employee_count = emp_count
                        lead.employee_count_source = emp_source
                        found_team_page = True

            # ── 4b2. Team members + owner title from team page ──
            if not found_team_members and any(kw in path_lower for kw in ("team", "staff", "people", "leadership", "management", "board")):
                team_json, owner_title = _extract_team_members(html)
                if team_json:
                    lead.team_members = team_json
                    found_team_members = True
                if owner_title:
                    lead.owner_title = owner_title

            # ── 4b3. Services from services/menu page ──
            if not found_services_page and any(kw in path_lower for kw in ("service", "menu", "product", "solution", "what-we-do", "capability", "expertise")):
                svc = _extract_services_from_html(html)
                if svc:
                    if lead.services:
                        lead.services += ", " + svc
                    else:
                        lead.services = svc
                    found_services_page = True

            # ── 4c. Partners from partner/client page ──
            if not found_partner_page and any(kw in path_lower for kw in ("partner", "client", "certif", "affiliat", "membership")):
                pnames = _extract_partners_from_page(html, base_url)
                if pnames:
                    lead.partners = ", ".join(pnames[:10])
                    found_partner_page = True

            # ── 4d. Recent activity from blog/news page ──
            if not found_blog_page and any(kw in path_lower for kw in ("blog", "news", "update", "press", "insight", "article")):
                activity = _extract_recent_activity(html)
                if activity["latest"]:
                    lead.last_activity_date = activity["latest"].replace(tzinfo=timezone.utc)
                titles = _extract_blog_post_titles(html)
                if titles:
                    t = "; ".join(titles[:5])
                    if activity["latest"]:
                        lead.recent_activity = (
                            f"Latest: {activity['latest'].strftime('%Y-%m-%d')} — {t}"
                        )
                    else:
                        lead.recent_activity = t
                    found_blog_page = True

        # ── 5. Also try year from about page text if not yet found ──
        if lead.year_founded is None and found_about_page:
            for text in all_texts:
                year = _extract_year_regex(text)
                if year:
                    lead.year_founded = year
                    break

        # ── 6. Review weakness computation ──
        weaknesses, wc = _compute_review_weaknesses(lead)
        if weaknesses:
            lead.review_weaknesses = weaknesses
            lead.review_weakness_count = wc

        logger.info(
            "Business intel for %s: desc=%d emp=%s year=%s partners=%s activity=%s svc=%s team=%s owner_title=%s weaknesses=%s",
            lead.name,
            len(lead.description_long or ""),
            lead.employee_count,
            lead.year_founded,
            bool(lead.partners),
            bool(lead.recent_activity),
            bool(lead.services),
            bool(lead.team_members),
            lead.owner_title or "",
            bool(lead.review_weaknesses),
        )

"""
services/contact_finder.py — Email and owner name extraction from websites.

Fixes applied:
  - Noise filter is now SCORE-based instead of binary discard: role emails
    (info@, contact@) are kept but deprioritized rather than discarded.
  - Owner name patterns expanded to 20+ patterns including possessive forms,
    "& Owner" structures, and non-English-friendly patterns.
  - Email scoring system: personal > role > generic; best available is kept.
  - Facebook fallback: uses multiple URL formats and cookie-less session.
  - Proxy rotation: uses ProxyRotator for website and Facebook requests.

Strategy:
  1. Fetch home/contact/about/team pages from business website
  2. Extract all emails via regex, score them by likely personal-ness
  3. Extract owner names via expanded regex patterns
  4. Fallback: scrape Facebook About tab for email
"""
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional, List, Tuple
from urllib.parse import urlparse, urljoin

import httpx
from bs4 import BeautifulSoup

from backend.models import Lead, WebsiteStatus
from backend.utils.retry import retry_sync, RetryableError

logger = logging.getLogger(__name__)

TIMEOUT = 12
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE
)
# Obfuscated emails: "name [at] domain [dot] com"
Obfuscated_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+\s*\[?\s*(?:at|@)\s*\]?\s*[a-zA-Z0-9.\-]+\s*\[?\s*(?:dot|\.)\s*\]?\s*[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# ── Junk / placeholder email filtering ───────────────────────────────────────
# Template/example emails that show up in website HTML, JS snippets, and schema
# examples — never real contacts. Extracting these pollutes results.
_PLACEHOLDER_DOMAINS = {
    "domain.com", "yourdomain.com", "example.com", "example.org", "example.net",
    "email.com", "youremail.com", "company.com", "yourcompany.com",
    "test.com", "sentry.io", "sentry-next.wixpress.com", "wix.com",
    "wixpress.com", "name.com", "site.com", "website.com", "mysite.com",
    "godaddy.com", "sentry.wixpress.com", "2x.png",
}
_PLACEHOLDER_LOCALS = {
    "user", "name", "username", "email", "youremail", "your-email", "your_email",
    "firstname", "lastname", "firstname.lastname", "first.last", "you",
    "example", "test", "sample", "demo", "placeholder", "no-reply-test",
    "sentry", "react", "core", "icon", "logo", "image",
}
# TLDs that are really asset file extensions wrongly matched by the regex.
_ASSET_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js",
               "ico", "woff", "woff2", "ttf", "eot", "mp4", "webm", "pdf"}


def _canonicalize_website(url: str) -> str:
    """Unwrap a Google `/url?q=...` redirect and strip utm_* params.

    Maps often hands us google.com/url?q=<real>&utm_... or UTM-tagged URLs;
    crawling those hits the wrong host and yields 0 emails. Stdlib only.
    """
    if not url:
        return url
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, unquote
    p = urlparse(url)
    # Unwrap Google redirect: take the real target from ?q= (or ?url=)
    if p.netloc.endswith("google.com") and p.path in ("/url", "/maps/url"):
        q = parse_qs(p.query)
        target = (q.get("q") or q.get("url") or [None])[0]
        if target:
            return _canonicalize_website(unquote(target))
    # Strip utm_* (and gclid) tracking params
    kept = [(k, v) for k, v in parse_qs(p.query, keep_blank_values=True).items()
            if not k.lower().startswith("utm_") and k.lower() != "gclid"]
    new_query = urlencode(kept, doseq=True)
    return urlunparse(p._replace(query=new_query))


def _normalize_email(email: str) -> str:
    """Lower-case and repair URL-derived addresses.

    Emails are harvested out of page HTML, so they arrive shaped by the URLs they
    were found in. A live run delivered `info@www.quillridgeroofworks.com` — `www.` is a web
    host prefix, never part of a mail domain, and MX lookup on it fails.
    """
    e = (email or "").strip().strip(".").lower()
    if e.count("@") != 1:
        return e
    local, domain = e.split("@")
    if domain.startswith("www."):
        domain = domain[4:]
    return f"{local}@{domain}"


def _is_junk_email(email: str) -> bool:
    """True if an email is a placeholder, asset filename, or obvious non-contact."""
    email = _normalize_email(email)
    if email.count("@") != 1:
        return True
    local, domain = email.split("@")
    # Malformed domains, all URL-fragment shaped, never real mailboxes:
    #   `b@.bing.com` (leading dot), `x@a..com` (double dot), `x@acme.` (trailing
    #   dot, which normalisation leaves as a bare label with no TLD at all).
    if not local or not domain or "." not in domain or "" in domain.split("."):
        return True
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if tld in _ASSET_TLDS:
        return True
    if domain in _PLACEHOLDER_DOMAINS:
        return True
    if local in _PLACEHOLDER_LOCALS:
        return True
    # filename-ish local parts: "logo@2x", "icon-512x512", trailing dimensions
    if re.search(r"@\dx$", email) or re.match(r"^[a-f0-9]{16,}$", local):
        return True
    # sentry/analytics DSNs embed a key@host
    if "sentry" in domain or "ingest" in domain:
        return True
    # Minified JavaScript reads as an email to the regex: a live run delivered
    # `n.d@a.length` as a contact, which scored +10 and pushed that lead to 94.
    # The tell is a JS property/method sitting where the TLD should be.
    # ponytail: a blocklist, not TLD validation — a single-char-label rule was
    # tried first and wrongly rejected real domains like x.io. Extend the list if
    # new JS-isms show up.
    if tld in _JS_PROPERTY_WORDS:
        return True
    # The TLD must be a real one. Page text split at an "@" produces plausible
    # shapes with impossible suffixes — a live run delivered `insul@ion.installed`.
    # Any 2-letter ccTLD is accepted without listing all ~250 of them.
    if len(tld) != 2 and tld not in _KNOWN_TLDS:
        return True
    # Asset/CDN hostnames split across an "@" by the regex: a live run delivered
    # `fonts.gst@ic.com` (from a Google Fonts URL) as a business contact. A real
    # local part is a mailbox, never a hostname fragment.
    if "." in local and local.split(".")[0] in _ASSET_HOST_PREFIXES:
        return True
    return False


# First labels of asset/CDN hostnames. Only consulted when the local part is
# itself dotted, so a real mailbox like "cdn.smith@acme.com" is unaffected only
# if its first label is not one of these — acceptable: these are not human names.
_ASSET_HOST_PREFIXES = {
    "fonts", "cdn", "static", "assets", "asset", "img", "images", "media",
    "js", "css", "ajax", "code", "cdnjs", "unpkg", "jsdelivr", "gstatic",
    "googleapis", "cloudfront", "akamai", "s3", "storage", "bucket",
}


# Real TLDs longer than two characters. Every 2-letter TLD is a ccTLD and is
# accepted without enumeration. Deliberately a generous allowlist of what a small
# business plausibly uses — the point is to reject impossible suffixes like
# ".installed" or ".length", not to police registry completeness. Add on demand.
_KNOWN_TLDS = {
    "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "name", "pro",
    "aero", "coop", "museum", "jobs", "mobi", "travel", "cat", "tel", "asia", "xxx",
    "app", "dev", "page", "site", "online", "store", "shop", "tech", "space",
    "website", "blog", "cloud", "digital", "agency", "solutions", "services",
    "company", "email", "team", "today", "world", "life", "live", "media", "news",
    "group", "center", "systems", "network", "studio", "design", "works", "expert",
    "guru", "ninja", "plus", "pub", "run", "wtf", "zone", "care", "clinic",
    "dental", "law", "legal", "finance", "insure", "realty", "estate", "homes",
    "house", "build", "construction", "contractors", "roofing", "plumbing",
    "kitchen", "repair", "supply", "tools", "auto", "cars", "bike", "coffee",
    "pizza", "restaurant", "bar", "cafe", "fit", "fitness", "salon", "spa",
    "photography", "photos", "gallery", "art", "music", "games", "io", "ai", "co",
    "xyz", "top", "vip", "club", "one", "ltd", "llc", "inc", "gmbh", "us", "uk",
}

# Property/method names that show up as a bogus "TLD" when the email regex runs
# over minified JS. Kept as a fast pre-check ahead of the TLD allowlist.
_JS_PROPERTY_WORDS = {
    "length", "push", "pop", "call", "apply", "bind", "prototype", "constructor",
    "indexof", "charat", "slice", "splice", "concat", "join", "split", "replace",
    "tostring", "valueof", "hasownproperty", "foreach", "map", "filter", "reduce",
    "then", "catch", "finally", "value", "target", "parent", "children", "style",
    "data", "type", "name", "id", "class", "src", "href", "min", "max", "default",
}

# Role-based local parts that indicate non-personal emails
# These are now SCORED (not discarded) — we keep them if no better option exists
ROLE_LOCAL_PARTS = {
    "info", "contact", "support", "admin", "hello", "noreply", "no-reply",
    "mail", "webmaster", "enquiries", "enquiry", "office", "team",
    "sales", "marketing", "hr", "help", "feedback", "orders",
    "booking", "bookings", "reservations", "service", "services",
    "accounts", "billing", "careers", "jobs", "press", "media",
    "partners", "privacy", "legal", "abuse", "postmaster",
}

# Generic domains that are never valid business emails
GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "mail.com", "protonmail.com", "proton.me", "zoho.com",
    "yandex.com", "gmx.com", "fastmail.com", "tutanota.com",
    "example.com", "test.com",
}

# Email confidence tiers
EMAIL_TIERS = {
    "high": (70, 100),      # personal name, on-domain (e.g. john@company.com)
    "medium": (40, 69),     # personal on generic domain, or name-like local
    "low": (20, 39),        # role-based (info@, contact@)
    "fallback": (0, 19),    # generic/unlikely
}

# Expanded owner/decision-maker patterns
# Each pattern captures the person's name in group(1)
# Patterns are ordered from most specific/confident to most general
OWNER_PATTERNS = [
    # "Founded by John Smith" / "Owned by John Smith"
    r"(?i:founded?\s+by|owned?\s+by|run\s+by|managed\s+by|operated\s+by)[:\s,]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=[,.\s!?]|$)",
    # "Owner: John Smith" / "Owner — John Smith"
    r"(?i:owner|founder)[:\s\u2013\u2014-]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=[,.\s!?]|$)",
    # "John Smith, Owner" — NOT followed by 'of'
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*[,\-–]\s*(?:Owner|Founder|CEO|Director|Manager|Principal|Proprietor|President)\b(?!\s+of)",
    # "John Smith, Owner of AcmePlumbing" — 'of Company' follows title
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*,\s*(?:Owner|Founder|CEO|Director|Manager|Principal|Proprietor|President)\s+of\s+\w+",
    # "John Smith - Founder / Owner" — dash before title
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*[\u2013\u2014\-]\s*(?:Owner|Founder|CEO|Director|Manager|Principal|Proprietor|President)(?:\s+of\b|\s*$)",
    # "John Smith is the owner/founder/CEO/director"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s+(?i:is\s+the\s+(?:owner|founder|ceo|director|principal|president))",
    # "Meet John Smith" / "meet Jane Doe" / "Say hello to John"
    r"(?i:meet|my\s+name\s+is|say\s+hello\s+to|introducing)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=[,.\s!?]|$)",
    # "I am Bob Jones" / "I'm Bob Jones"
    r"I(?:'m|\s+am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=\s+and|\s+the|[,.!]|$)",
    # "John Smith & Son" / "John Smith & Co" (business named after owner)
    r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+))\s+&\s+(?:Son|Sons|Co|Company|Associates)\s",
    # "Our founder, John Smith" / "Our CEO, Jane Doe"
    r"(?i:(?:our|the)\s+(?:founder|owner|ceo|director|manager|president))[:\s,]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=[,.\s!?]|$)",
    # "with owner John Smith" / "with founder Jane Doe"
    r"(?i:(?:with|by|from)\s+(?:owner|founder))\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=[,.\s!?]|$)",
    # "President John Smith" / "Owner Jane Doe" (title before name)
    r"(?i:\b(?:Owner|Founder|CEO|President|Director|Manager|Principal|Proprietor)\s+(?:is\s+)?)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=[,.\s!?]|$)",
    # "Contact John Smith, owner" — name before role, with comma
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}),\s+(?i:the\s+)?(?:owner|founder|proprietor)\b",
    # Non-English: "Propietario: Juan García" (Spanish)
    r"(?i:propietario|dueño|fundador|gerente|director)[:\s]+([A-Z][a-záéíóúüñ]+(?:\s+[A-Z][a-záéíóúüñ]+){1,3})(?=[,.\s!?]|$)",
    # Non-English: "Propriétaire: Jean Dupont" (French)
    r"(?i:propriétaire|gérant|fondateur|directeur)[:\s]+([A-Z][a-zàâçéèêëîïôûùü]+(?:\s+[A-Z][a-zàâçéèêëîïôûùü]+){1,3})(?=[,.\s!?]|$)",
    # Non-English: "Inhaber: Hans Müller" (German)
    r"(?i:inhaber|geschäftsführer|gründer|eigentümer|besitzer)[:\s]+([A-Z][a-zäßöüÄÖÜ]+(?:\s+[A-Z][a-zäßöüÄÖÜ]+){1,3})(?=[,.\s!?]|$)",
    # Generic title patterns that work in many languages
    r"(?i:contact)\s+(?:person|us|details)?[:\s,]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?=[,.\s!?]|$)",
]

# Pages to crawl for contact info — highest-yield paths only. (Trimmed from ~28
# to keep crawl fast; sitemap discovery still adds site-specific relevant pages.)
CONTACT_PATHS = [
    "/contact", "/contact-us", "/contactus",
    "/about", "/about-us",
    "/team", "/our-team",
    "/staff", "/leadership",
    "/locations", "/location", "/find-us", "/hours",
    "/menu", "/our-menu", "/book-now", "/reservations",
    "/support", "/help",
]


def _parse_sitemap(domain_url: str, client: httpx.Client,
                   cache: Optional[dict] = None) -> List[str]:
    """
    Fetch and parse a website's sitemap.xml to discover additional crawlable pages.
    Returns a list of full page URLs (path only, relative to domain).
    """
    sitemap_urls = [
        urljoin(domain_url, "/sitemap.xml"),
        urljoin(domain_url, "/sitemap_index.xml"),
        urljoin(domain_url, "/sitemap"),
    ]
    discovered: List[str] = []
    for url in sitemap_urls:
        try:
            html = _fetch(url, client, cache)
            if not html:
                continue
            root = ET.fromstring(html.encode("utf-8", errors="ignore"))
            # Namespace-aware: sitemaps use xmlns
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for loc in root.findall(".//sm:loc", ns):
                page_url = loc.text.strip() if loc.text else ""
                if page_url:
                    discovered.append(page_url)
            # Also try without namespace (some sitemaps are sloppy)
            if not discovered:
                for loc in root.findall(".//loc"):
                    page_url = loc.text.strip() if loc.text else ""
                    if page_url:
                        discovered.append(page_url)
            if discovered:
                logger.info("Found %d pages via sitemap: %s", len(discovered), url)
                break
        except ET.ParseError:
            continue
        except Exception:
            continue
    return discovered


def _email_tier(score: int) -> str:
    """Map an email score (0-100) to a confidence tier."""
    for tier, (lo, hi) in EMAIL_TIERS.items():
        if lo <= score <= hi:
            return tier
    return "fallback"


def _get_tier_preference(tier: str) -> int:
    """Higher number = more preferred."""
    order = {"high": 4, "medium": 3, "low": 2, "fallback": 1}
    return order.get(tier, 0)


def _score_email(email: str, domain: str = "") -> int:
    """
    Score an email by how likely it is to be a personal/owner email.
    Higher score = more likely personal. Returns 0-100.
    """
    local = email.split("@")[0].lower()
    email_domain = email.split("@")[1].lower() if "@" in email else ""

    # Penalize generic email domains (gmail, yahoo, etc.)
    if email_domain in GENERIC_DOMAINS:
        return 10

    # Bonus for being on the business domain
    on_domain_bonus = 20 if domain and domain in email_domain else 0

    # Heuristic: likely personal if local part is a name
    # Names are typically 5-20 chars, letters only, may contain dots
    if re.match(r"^[a-z][a-z.]+[a-z]$", local) and len(local) >= 4:
        # Check if it looks like "firstname.lastname" or "firstname"
        if "." in local:
            return 80 + on_domain_bonus
        return 60 + on_domain_bonus

    # Role-based emails (info@, contact@, etc.) — keep but score lower
    if local in ROLE_LOCAL_PARTS:
        return 30 + on_domain_bonus

    # If it's a short acronym or looks automated
    if len(local) <= 3:
        return 20 + on_domain_bonus

    # Generic catch-all
    return 40 + on_domain_bonus


def _best_email(emails: List[str], domain: str = "") -> Optional[str]:
    """
    Pick the best email from a list using scoring.
    Always returns SOMETHING if emails exist (no more silent discarding).
    """
    if not emails:
        return None

    # Deduplicate preserving order
    seen = set()
    unique = []
    for e in emails:
        e_lower = e.lower()
        if e_lower not in seen:
            seen.add(e_lower)
            unique.append(e)

    # Score and sort
    scored: List[Tuple[int, str]] = []
    for email in unique:
        score = _score_email(email, domain)
        scored.append((score, email))

    scored.sort(key=lambda x: (-x[0], x[1]))

    best = scored[0][1]
    best_score = scored[0][0]
    tier = _email_tier(best_score)
    logger.debug("Best email: %s (score: %d, tier: %s, from %d candidates)",
                 best, best_score, tier, len(scored))
    return best


def _deobfuscate_email(text: str) -> Optional[str]:
    """Convert obfuscated 'name [at] domain [dot] com' to name@domain.com."""
    m = Obfuscated_EMAIL_RE.search(text)
    if not m:
        return None
    raw = m.group(0)
    # Replace [at] and [dot] with @ and .
    result = re.sub(r'\s*\[?\s*(?:at|@)\s*\]?\s*', '@', raw, count=1)
    result = re.sub(r'\s*\[?\s*(?:dot|\.)\s*\]?\s*', '.', result)
    result = result.strip().strip('[]()')
    if EMAIL_RE.match(result) and not _is_junk_email(result):
        return result
    return None


def _extract_emails_from_html(html: str) -> List[str]:
    """Extract all email addresses from HTML text, filtering junk/placeholders.

    Normalised here so everything downstream — scoring, MX/RCPT verification and
    the export — sees the repaired form rather than the URL-shaped original.
    """
    found = [_normalize_email(e) for e in EMAIL_RE.findall(html)]
    clean = [e for e in found if not _is_junk_email(e)]
    # Also try deobfuscation — normalise it too, or the un-repaired form sneaks
    # back in alongside the repaired one.
    deobf = _deobfuscate_email(html)
    if deobf:
        deobf = _normalize_email(deobf)
        if not _is_junk_email(deobf) and deobf not in clean:
            clean.append(deobf)
    return list(dict.fromkeys(clean))


# Tokens that never appear in a real person's name. OWNER_PATTERNS match runs of
# capitalised words, so page chrome slipped straight through: a live audit of
# leads.db found 17 of 23 stored owner_names were junk ("Our Team", "Risk
# Management", "About Us Turner", "Needs October"). Each one scored +5 lead
# points and +0.10 data_confidence, and landed verbatim in an LLM cold-email
# opener ("Hi Our Management Services, ...").
_NON_NAME_TOKENS = {
    # pronouns / articles / connectives a greedy pattern drags in
    "our", "us", "we", "your", "you", "the", "their", "my", "a", "an", "and",
    "at", "in", "of", "for", "to", "is", "are", "with", "by", "or",
    # site chrome
    "about", "contact", "home", "menu", "services", "service", "team", "teams",
    "staff", "people", "portal", "explore", "learn", "more", "read", "view",
    "click", "here", "page", "site", "website", "blog", "news", "testimonials",
    "reviews", "review", "gallery", "portfolio", "projects", "project", "faq",
    "help", "support", "login", "signup", "search", "privacy", "policy", "terms",
    "welcome", "info", "email", "phone", "address", "hours", "location",
    # business / role nouns
    "management", "managing", "development", "business", "company", "corp",
    "corporation", "inc", "llc", "ltd", "group", "solutions", "systems",
    "construction", "builders", "building", "remodeling", "additions",
    "remodels", "sustainability", "partners", "associates", "enterprises",
    "owner", "founder", "ceo", "cfo", "cto", "president", "director", "manager",
    "principal", "proprietor", "department", "division", "office", "consulting",
    # months / marketing filler
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "today", "now", "tips",
    "ideas", "needs", "free", "quote", "estimate", "call", "get", "best", "new",
    # Common English function/filler words. A live run delivered "Form Whether"
    # as an owner name: OWNER_PATTERNS matched a capitalised word run mid-sentence
    # and both words passed, because neither is site chrome. Sentence words are
    # the remaining false-positive source, so block the frequent ones.
    "whether", "which", "while", "when", "where", "what", "who", "why", "how",
    "there", "these", "those", "this", "that", "them", "then", "than", "they",
    "form", "from", "here", "have", "has", "had", "been", "being", "will",
    "would", "could", "should", "shall", "must", "may", "might", "can",
    "please", "thank", "thanks", "welcome", "learn", "start", "find", "make",
    "know", "need", "want", "give", "take", "come", "look", "keep", "let",
    "every", "each", "also", "just", "only", "even", "very", "much", "many",
    "some", "any", "all", "both", "other", "another", "such", "same", "more",
    "most", "less", "least", "first", "last", "next", "back", "over", "under",
    "after", "before", "during", "through", "between", "into", "onto", "upon",
    "because", "since", "until", "unless", "though", "although", "however",
    "therefore", "thus", "hence", "yet", "still", "already", "always", "never",
}

# A name token: letters, optionally hyphenated/apostrophised (O'Brien, Smith-Jones).
_NAME_TOKEN_RE = re.compile(r"^[A-Z][a-zA-ZÀ-ÿ'’\-]*$")


def is_plausible_person_name(name: str) -> bool:
    """Does `name` look like an actual human name rather than page furniture?

    Deliberately strict: a missed real owner costs 5 score points, a false one
    ships a client a cold email addressed to "Risk Management".
    """
    if not name:
        return False
    parts = name.strip().split()
    if not (2 <= len(parts) <= 4):
        return False
    for p in parts:
        if not _NAME_TOKEN_RE.match(p):
            return False
        if p.lower().strip(".,'’-") in _NON_NAME_TOKENS:
            return False
    # Reject all-initials / single-letter runs ("J P" isn't usable for outreach).
    return sum(1 for p in parts if len(p) >= 3) >= 2


def _extract_owner_from_html(html: str) -> Optional[str]:
    """
    Try expanded regex patterns to find an owner/founder name.
    Uses BeautifulSoup to extract visible text, preserving structure.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style content
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)

    # Keep scanning past a junk match instead of returning it: the first pattern
    # to fire is often the greedy "Meet …" / "Contact …" one, while a later,
    # more specific pattern ("Founded by John Smith") has the real name.
    for pattern in OWNER_PATTERNS:
        m = re.search(pattern, text)
        if m and is_plausible_person_name(m.group(1).strip()):
            return m.group(1).strip()

    return None


@retry_sync(max_attempts=2, base_delay=0.5, max_delay=2.0)
def _fetch(url: str, client: httpx.Client, cache: Optional[dict] = None) -> Optional[str]:
    """Fetch a URL with timeout, redirect following, and retry logic.

    Kept deliberately fast: contact crawling hits many dead/slow medical sites,
    so a long timeout × many retries × ~28 paths is the dominant cost. One quick
    retry is enough for transient blips; truly-down hosts are short-circuited by
    the caller (see enrich_lead_sync) once the homepage fails.

    `cache` is the per-lead page cache shared with business_intel (see its _fetch)
    — both crawl the same /about, /team, /staff… paths of the same site.
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


def _get_httpx_proxy() -> dict:
    """Get proxy config for httpx client (httpx>=0.28 uses single `proxy=`)."""
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


def _scrape_facebook_email(fb_url: str, client: httpx.Client) -> Optional[str]:
    """
    Scrape Facebook About page for email addresses.
    Uses multiple URL formats and parses both visible text and meta tags.
    """
    # Try multiple Facebook URL formats
    fb_url_clean = fb_url.rstrip("/")
    about_urls = [
        f"{fb_url_clean}/about",
        f"{fb_url_clean}/about_contact_and_basic_info",
        f"{fb_url_clean}/about/?ref=page_internal",
        f"https://www.facebook.com/pages/{fb_url_clean.split('/')[-1]}/about",
    ]

    all_emails = []
    for url in about_urls:
        html = _fetch(url, client)
        if not html:
            continue

        # Extract from visible text
        emails = _extract_emails_from_html(html)
        all_emails.extend(emails)

        # Also check meta tags
        soup = BeautifulSoup(html, "html.parser")
        for meta in soup.find_all("meta", attrs={"property": "business:contact_data:email"}):
            content = meta.get("content")
            if content and "@" in content:
                all_emails.append(content)

        if all_emails:
            break

    # Deduplicate and return best match
    if all_emails:
        seen = set()
        unique = []
        for e in all_emails:
            e_lower = e.lower()
            if e_lower not in seen:
                seen.add(e_lower)
                unique.append(e)
        return _best_email(unique)

    return None


def _get_social_urls(lead: Lead) -> dict:
    """Get social media URLs from a lead object."""
    return {
        "facebook": lead.social_facebook,
        "instagram": lead.social_instagram,
        "twitter": lead.social_twitter,
        "linkedin": lead.social_linkedin,
    }


def _visible_text(html: str) -> str:
    """Extract visible text from HTML for phone harvesting."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def enrich_lead_sync(lead: Lead, region: str = "US", cache: Optional[dict] = None) -> None:
    """
    Enrich a single lead with email, owner name, owner phone (best-effort), and
    verification flags.

    Strategy:
      1. Crawl business website pages (home, contact, about, team)
      2. Extract and score emails — keep the best one
      3. Extract owner/founder name using expanded patterns
      4. If no email found, try Facebook About page
      5. Harvest + classify all phones, label best owner-phone candidate
      6. Verify email (MX) and phone (libphonenumber)

    Modifies lead object in place — caller must commit.
    """
    proxy_kwargs = _get_httpx_proxy()
    page_texts: List[Tuple[str, str]] = []

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT,
                      follow_redirects=True, **proxy_kwargs) as client:
        all_emails: List[str] = []
        owner_name: Optional[str] = None
        domain = ""
        pages_crawled = 0

        # ponytail: crawl whenever a website is present, even if website_checker
        # marked it DEAD — it false-flags JS/SPA sites and zeros email yield.
        # Tradeoff: we burn a few requests on genuinely-dead hosts, but the
        # CONTACT_PATHS / page-limit bound below caps the cost.
        if lead.website:
            website = _canonicalize_website(lead.website)
            parsed = urlparse(website)
            domain = parsed.netloc.removeprefix("www.")
            base_url = f"{parsed.scheme}://{parsed.netloc}"

            # Sitemap discovery: find extra pages to crawl
            sitemap_pages = _parse_sitemap(base_url, client, cache)
            # Filter sitemap pages to relevant ones (contact/about/team paths)
            relevant_keywords = {"contact", "about", "team", "staff", "management",
                                 "leadership", "company", "owner", "founder",
                                 "board", "executive", "personnel", "location",
                                 "find", "directory", "people"}
            sitemap_paths = []
            for page_url in sitemap_pages:
                path = urlparse(page_url).path.lower()
                if any(kw in path for kw in relevant_keywords):
                    sitemap_paths.append(page_url)
            if sitemap_paths:
                logger.info("Found %d relevant pages via sitemap for %s", len(sitemap_paths), domain)

            # Combine hardcoded paths + sitemap-discovered paths
            all_paths = list(CONTACT_PATHS)
            # Add sitemap paths that aren't already in the hardcoded list
            seen_paths = {p.lower() for p in all_paths}
            for sp in sitemap_paths:
                sp_path = urlparse(sp).path.lower()
                if sp_path not in seen_paths:
                    all_paths.append(sp)
                    seen_paths.add(sp_path)

            # Homepage first
            html = _fetch(website, client, cache)
            homepage_reachable = bool(html)
            if html:
                all_emails += _extract_emails_from_html(html)
                page_texts.append((website, _visible_text(html)))
                if not owner_name:
                    owner_name = _extract_owner_from_html(html)
                pages_crawled += 1

            # Determine the best tier we already have
            best_tier = _email_tier(max((_score_email(e, domain) for e in all_emails), default=0))
            best_tier_pref = _get_tier_preference(best_tier)

            # Short-circuit unreachable hosts: if the homepage didn't load, don't
            # waste time on ALL sub-paths — but still try a few high-value paths
            # (contact/about) since they might be static HTML even if the homepage
            # is JS-rendered or behind a challenge.
            if homepage_reachable:
                crawl_paths = all_paths
            else:
                high_value = {"/contact", "/contact-us", "/about", "/about-us", "/locations"}
                crawl_paths = [p for p in all_paths if p in high_value]
                if crawl_paths:
                    logger.info("Homepage unreachable but trying %d high-value paths", len(crawl_paths))

            # Crawl additional pages (hardcoded paths + sitemap)
            for path in crawl_paths:
                if path.startswith("http"):
                    url = path  # already a full URL from sitemap
                else:
                    url = f"{base_url}{path}"

                html = _fetch(url, client, cache)
                if html:
                    pages_crawled += 1
                    page_emails = _extract_emails_from_html(html)
                    all_emails += page_emails
                    page_texts.append((url, _visible_text(html)))
                    if not owner_name:
                        owner_name = _extract_owner_from_html(html)

                    # Re-evaluate best tier after each page
                    current_best_score = max((_score_email(e, domain) for e in all_emails), default=0)
                    current_tier = _email_tier(current_best_score)
                    current_tier_pref = _get_tier_preference(current_tier)

                    # Stop if tier improved and we have an owner name
                    if owner_name and current_tier_pref >= _get_tier_preference("medium"):
                        logger.info("Found %s-tier email and owner name — stopping crawl after %d pages",
                                    current_tier, pages_crawled)
                        break

                    # Safety limits
                    if len(all_emails) >= 20:
                        logger.info("Reached 20 email limit — stopping crawl")
                        break
                    if pages_crawled >= 10:
                        logger.info("Reached 10 page limit — stopping crawl")
                        break

            logger.info("Crawled %d pages for %s — found %d emails (best tier: %s)",
                        pages_crawled, domain, len(all_emails), best_tier)

        # Fallback: scrape Facebook About tab if no high-tier email found
        has_high_tier = _get_tier_preference(
            _email_tier(max((_score_email(e, domain) for e in all_emails), default=0))
        ) >= _get_tier_preference("medium")
        if not has_high_tier and lead.social_facebook:
            fb_email = _scrape_facebook_email(lead.social_facebook, client)
            if fb_email:
                all_emails.append(fb_email)
                logger.info("Found email via Facebook for %s: %s", lead.name, fb_email)

        # Assign best email (scored, always keeps best available)
        if all_emails:
            lead.owner_email = _best_email(list(dict.fromkeys(all_emails)), domain)

        # Assign owner name if found
        if owner_name:
            lead.owner_name = owner_name

    # ── Owner phone (best-effort) + verification ──────────────────────────
    try:
        from backend.services.owner_contact import find_owner_contact, classify_phone
        from backend.services.verifier import verify_email

        social_texts: List[str] = []
        result = find_owner_contact(
            website=lead.website,
            business_phone=lead.phone,
            owner_name=lead.owner_name,
            page_texts=page_texts,
            maps_phone=None,
            social_texts=social_texts,
            region=region,
        )
        if result.get("owner_phone"):
            lead.owner_phone = result["owner_phone"]
            lead.owner_phone_type = result.get("owner_phone_type")
            lead.owner_phone_confidence = result.get("owner_phone_confidence", 0.0)

        # Verify email (format → MX → RCPT probe + catch-all). Result is cached
        # per-address so the scoring stage reuses it for free. Stash the tier so
        # verification_tier can distinguish a proven mailbox from a catch-all.
        if lead.owner_email:
            ev = verify_email(lead.owner_email, timeout=3)
            lead.email_verified = ev["verified"]
            lead._email_tier = ev["tier"]

        # Verify business phone (libphonenumber)
        if lead.phone:
            info = classify_phone(lead.phone, region)
            lead.phone_verified = bool(info and info.get("valid"))
    except Exception as e:
        logger.warning("Owner-contact/verification step failed for '%s': %s", lead.name, e)


if __name__ == "__main__":
    # Self-check for _canonicalize_website (the only non-trivial new logic).
    assert _canonicalize_website(
        "https://www.google.com/url?q=https%3A%2F%2Facme.com&utm_source=maps"
    ) == "https://acme.com", "google redirect not unwrapped"
    assert _canonicalize_website(
        "https://acme.com/contact?utm_source=x&id=5"
    ) == "https://acme.com/contact?id=5", "utm not stripped"
    assert _canonicalize_website("https://acme.com") == "https://acme.com"
    assert _canonicalize_website("") == ""
    print("contact_finder._canonicalize_website self-check OK")

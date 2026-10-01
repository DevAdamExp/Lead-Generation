"""
hotfrog_core.py — Hotfrog scraping with multi-IP proxy rotation.

Strategy:
  1. PRIMARY: Use Playwright (headless Chromium) to bypass Cloudflare/bot detection.
     Playwright handles JS challenges that httpx cannot.
  2. FALLBACK: httpx with rotating proxies and session warmup.
  3. Data extraction: JSON-LD structured data (preferred), then CSS selectors.
  4. PROXY: Uses ProxyRotator for multi-IP rotation across Tor instances.

Fixes applied:
  - Bug #hotfrog_url: Now correctly preserves Hotfrog profile URL separate from
    business website (was overwriting hotfrog_url with business website URL).
  - Pagination: Parses page navigation from HTML; detects total pages and
    "no results" state.
  - Proxy rotation: Integrates with ProxyRotator for multi-IP via Tor.
  - Selector robustness: Multiple CSS fallback strategies per field.
  - Website resolution: Fixed url/sameAs precedence logic in JSON-LD parsing.
  - Social links: Preserved from both search results and detail pages.
  - Challenge bypass: Improved Turnstile/ALTCHA handling with retry logic.
"""
import json
import random
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TIMEOUT = 30
BASE = "https://www.hotfrog.com"

# Process-level circuit breaker: once Hotfrog hard-blocks (HTTP 403/429), every
# further request will too, and the Playwright+challenge fallback can't solve a
# hard block — it just burns ~40-60s per page. So once we see a hard block we
# stop hitting Hotfrog for the rest of the process and fail fast.
_HARD_BLOCKED = False


def reset_block_state() -> None:
    """Clear the circuit breaker (e.g. after rotating to a fresh IP/run)."""
    global _HARD_BLOCKED
    _HARD_BLOCKED = False


# Cloudflare interstitial fingerprints — if the fetched HTML is one of these,
# it's the challenge page, NOT real content (httpx sometimes gets a 200 with it).
_CHALLENGE_MARKERS = (
    "just a moment",
    "challenges.cloudflare.com",
    "cf-challenge",
    "cf_chl_opt",
    "attention required",
    "enable javascript and cookies to continue",
)


def _looks_like_challenge(html: str) -> bool:
    if not html:
        return True
    head = html[:4000].lower()
    return any(m in head for m in _CHALLENGE_MARKERS)


class _ResultList(list):
    """A list subclass that can carry a `_pagination` attribute."""
    _pagination = None

_USER_AGENTS = [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "Gecko/20100101 Firefox/128.0"),
]

_SOCIAL_DOMAINS = (
    "facebook.com", "instagram.com", "twitter.com", "linkedin.com", "x.com",
    "yelp.com", "yellowpages.com", "hotfrog.com",
)

# Multiple selector strategies per field — tried in order
_SELECTORS = {
    "card": [
        "div.hf-box",
        "div.company-card",
        "li.search-result",
        "div[class*='business']",
        "article",
        "div.search-result-item",
    ],
    "card_link": [
        'h3 a[href*="/company/"]',
        'a[href*="/company/"][class*="title"]',
        'h2 a[href*="/company/"]',
        'a[href*="/company/"]',
        'h3 a',
    ],
    "card_phone": [
        'a[href^="tel:"]',
        '[class*="phone"]',
        '[class*="tel"]',
    ],
    "card_address": [
        "span.small",
        "[class*='address']",
        "[class*='location']",
    ],
    "detail_name": [
        "h1",
        "[class*='business-name']",
        "[class*='company-name']",
        "h2",
    ],
    "detail_description": [
        ".description",
        ".company-description",
        "[class*='about']",
        "[class*='description']",
        "meta[name='description']",
    ],
}


# ── HTML Parsers ──────────────────────────────────────────────────────────────

def _iter_jsonld(soup):
    """Yield every JSON object found in JSON-LD script tags (handles @graph)."""
    from bs4 import BeautifulSoup
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                if "data" in item and isinstance(item["data"], dict):
                    yield item["data"]
                if "@graph" in item:
                    for g in item["@graph"]:
                        if isinstance(g, dict):
                            yield g
                else:
                    yield item


def _is_business(obj):
    t = obj.get("@type", "")
    types = t if isinstance(t, list) else [t]
    return any(
        "Business" in str(x) or x in ("Organization", "Store", "Restaurant", "ProfessionalService", "LocalBusiness", "Service")
        for x in types
    )


def _is_hotfrog_url(url: str) -> bool:
    """Check if a URL is a Hotfrog page URL (not an external business website)."""
    if not url:
        return False
    lower = url.lower()
    return (
        "hotfrog.com" in lower
        or "/company/" in lower
        or "/business/" in lower
        or "/listing/" in lower
    )


def _norm_business(obj) -> dict:
    """
    Normalize a JSON-LD business object.
    Correctly separates Hotfrog profile URL from actual business website.
    """
    from bs4 import BeautifulSoup

    addr = obj.get("address")
    if isinstance(addr, dict):
        parts = filter(None, [
            addr.get("streetAddress"),
            addr.get("addressLocality"),
            addr.get("addressRegion"),
            addr.get("postalCode"),
            addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else None,
        ])
        addr = ", ".join(parts)

    raw_url = obj.get("url") or ""
    same_as = obj.get("sameAs")

    # Normalize sameAs to a single URL string
    if isinstance(same_as, list):
        same_as = next(
            (u for u in same_as if isinstance(u, str) and u.startswith("http")),
            None,
        )
    elif not isinstance(same_as, str):
        same_as = None

    # Determine Hotfrog profile URL
    # In JSON-LD on Hotfrog, 'url' is usually the Hotfrog page itself
    hotfrog_url = raw_url if _is_hotfrog_url(raw_url) else None

    # Determine actual business website
    # Prefer sameAs if it's not a social/hotfrog domain
    website = None
    if same_as and not any(s in same_as.lower() for s in _SOCIAL_DOMAINS):
        website = same_as
    # Fall back to url if it's NOT a hotfrog URL (e.g., the business's own site)
    if not website and raw_url and not _is_hotfrog_url(raw_url) and not any(s in raw_url.lower() for s in _SOCIAL_DOMAINS):
        website = raw_url

    return {
        "name": obj.get("name"),
        "address": addr,
        "phone": obj.get("telephone"),
        "website": website,
        "hotfrog_url": hotfrog_url,
        "description": obj.get("description"),
        "social_facebook": _extract_social(same_as, "facebook"),
        "social_instagram": _extract_social(same_as, "instagram"),
        "social_twitter": _extract_social(same_as, "twitter"),
        "social_linkedin": _extract_social(same_as, "linkedin"),
    }


def _extract_social(url_or_list, platform: str) -> Optional[str]:
    """Extract a social URL if it matches the given platform."""
    if not url_or_list:
        return None
    urls = url_or_list if isinstance(url_or_list, list) else [url_or_list]
    for u in urls:
        if isinstance(u, str) and platform in u.lower():
            return u
    return None


def _first_text(soup, *selectors):
    """Return text from the first matching CSS selector."""
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return txt
    return None


def _parse_pagination(html: str) -> dict:
    """Detect `total_pages` — the ONLY field the caller reads (sources.py stops
    the crawl once it reaches it). The old parser also computed current_page /
    has_next / next_url / total_results / is_empty / result_count_text, none of
    which anything consumed, and the crawl already stops when a page yields no
    NEW businesses. So this is intentionally just a best-effort largest-page scan.
    """
    from bs4 import BeautifulSoup
    import re

    soup = BeautifulSoup(html, "html.parser")
    page_nums: set[int] = set()
    # Numbered links in any pagination container + ?page= / /page/ style hrefs.
    for a in soup.select("[class*='pagination'] a, nav a, a[href*='page']"):
        txt = a.get_text(strip=True)
        if txt.isdigit():
            page_nums.add(int(txt))
        for n in re.findall(r"(?:page|pg)[=/](\d+)", (a.get("href") or "").lower()):
            page_nums.add(int(n))
    total = max((n for n in page_nums if 1 <= n <= 10000), default=None)
    return {"total_pages": total}


# ── Fetch with proxy rotation ────────────────────────────────────────────────

def _get_proxy_config(for_playwright: bool = True) -> Optional[dict]:
    """Get proxy config from the ProxyRotator, with graceful fallback."""
    try:
        from backend.services.proxy_rotator import get_rotator
        rotator = get_rotator()
        return rotator.get_proxy(for_playwright=for_playwright)
    except Exception:
        return None


def _rotate_ip():
    """Try to rotate to a new IP."""
    try:
        from backend.services.proxy_rotator import get_rotator
        rotator = get_rotator()
        return rotator.rotate_ip(force_new=False)
    except Exception:
        return False


# ── Playwright fetcher ────────────────────────────────────────────────────────

def _fetch_with_playwright(url: str, proxy_config: Optional[dict] = None) -> Optional[str]:
    """
    Fetch page HTML using Playwright with optional proxy.
    Uses a new event loop to be safe in Celery forked workers.
    """
    import asyncio

    async def _run():
        from playwright.async_api import async_playwright
        try:
            from playwright_stealth import Stealth
            _stealth = True
        except ImportError:
            _stealth = False

        import os
        headless_env = os.getenv("SCRAPER_HEADLESS", "true").lower()
        headless = headless_env in ("true", "1", "yes")

        async with async_playwright() as p:
            launch_kwargs = {
                "headless": headless,
                "args": [
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            }
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=random.choice(_USER_AGENTS),
                viewport={"width": 1280, "height": 720},
                locale="en-US",
                permissions=["geolocation"],
            )
            page = await context.new_page()
            if _stealth:
                await Stealth().apply_stealth_async(page)

            try:
                # Warm session — visit homepage first
                nav_timeout = 30000 if proxy_config else 15000
                await page.goto(BASE + "/", timeout=nav_timeout, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(1.0, 2.5))
                await page.goto(url, timeout=40000, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(2.0, 3.5))

                # Handle potential challenge pages
                title = await page.title()
                if any(phrase in title for phrase in _CHALLENGE_TITLES):
                    logger.info("Challenge page detected for %s. Title: %s", url, title)
                    solved = await _solve_challenge(page)
                    if solved:
                        await asyncio.sleep(2.0)
                        return await page.content()
                    else:
                        logger.warning("Could not solve challenge for %s", url)
                        return None

                return await page.content()

            except Exception as e:
                logger.warning("Playwright fetch failed for %s: %s", url, e)
                return None
            finally:
                await browser.close()

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception as e:
        logger.warning("Playwright event loop error: %s", e)
        return None


_CHALLENGE_TITLES = ("Verification Required", "Just a moment",
                     "Attention Required", "Challenge")


async def _solve_challenge(page) -> bool:
    """Wait out a Cloudflare managed challenge.

    Cloudflare's Turnstile/managed challenge resolves ITSELF once its JS runs —
    the only thing that reliably helps is waiting and re-checking the title.
    Clicking the widget (the old iframe/ALTCHA/verify-button strategies) can't
    reliably solve Turnstile and mostly just burned wall-clock, so it's gone.
    Poll ~2s × 8 (16s ceiling): if it hasn't cleared by then it's a hard block
    that won't clear, and _fetch_page trips the breaker to fail the rest fast.
    """
    import asyncio

    for i in range(1, 9):
        await asyncio.sleep(2.0)
        title = await page.title()
        if not any(p in title for p in _CHALLENGE_TITLES):
            logger.info("Challenge cleared after ~%ds", i * 2)
            await asyncio.sleep(1.0)
            return True
    return False


# ── httpx fetcher ─────────────────────────────────────────────────────────────

def _fetch_with_httpx(url: str, proxy_config: Optional[dict] = None) -> Optional[str]:
    """Fetch page HTML using httpx with optional proxy configuration."""
    import httpx

    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
    }
    try:
        client_kwargs = {
            "headers": headers,
            "timeout": TIMEOUT,
            "follow_redirects": True,
        }
        if proxy_config:
            # httpx >= 0.28 uses a single `proxy=` URL string.
            # NOTE: must not shadow the `url` page parameter.
            proxy_url = proxy_config.get("all://") or next(iter(proxy_config.values()), None)
            if proxy_url:
                client_kwargs["proxy"] = proxy_url

        client = httpx.Client(**client_kwargs)
        # Warm session
        client.get(BASE + "/", timeout=10)
        time.sleep(random.uniform(0.8, 1.5))
        client.headers.update({"Referer": BASE + "/", "Sec-Fetch-Site": "same-origin"})
        resp = client.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError as e:
        # A 403/503 from Hotfrog is almost always a Cloudflare "Just a moment"
        # JS challenge — httpx CAN'T solve it, but Playwright CAN. So DON'T trip
        # the circuit breaker here (that used to skip the browser fallback and
        # kill Hotfrog outright). Just return None and let _fetch_page try the
        # browser; the breaker trips only if the browser ALSO fails.
        code = e.response.status_code if e.response is not None else 0
        logger.info("httpx fetch failed for %s (HTTP %s) — will try browser", url, code)
        return None
    except Exception as e:
        logger.warning("httpx fetch failed for %s: %s", url, e)
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def _fetch_page(url: str, use_playwright: bool = False, rotate_proxy: bool = False) -> Optional[str]:
    """
    Fetch a page with proxy rotation and dual-strategy fallback.

    Strategy:
      1. Try httpx first (lightweight, fast)
      2. If blocked/failed, try Playwright (handles JS challenges)
      3. Each attempt uses a proxy from the rotation pool (if available)

    Args:
        url: The URL to fetch.
        use_playwright: If True, try Playwright first.
        rotate_proxy: If True, rotate IP before fetching.
    """
    global _HARD_BLOCKED

    if rotate_proxy:
        _rotate_ip()
        # Fresh IP = fresh chance — reset the circuit breaker. The old IP's
        # block shouldn't poison the new IP's requests.
        reset_block_state()

    # Circuit breaker: the browser ALREADY failed to solve Hotfrog on the
    # current IP this run. Until we rotate, fail fast instead of burning ~40-60s
    # per page on a challenge the browser can't beat from this IP.
    if _HARD_BLOCKED:
        return None

    # 1) Cheap httpx attempt first (fast when it works). A 403/503 here is a
    #    Cloudflare JS challenge httpx can't solve — it returns None, and we
    #    fall through to the browser rather than giving up.
    if not use_playwright:
        proxy = _get_proxy_config(for_playwright=False)
        html = _fetch_with_httpx(url, proxy_config=proxy)
        if html and not _looks_like_challenge(html):
            return html

    # 2) Browser attempt — Playwright can solve the CF "Just a moment" challenge.
    pw_proxy = _get_proxy_config(for_playwright=True)
    html = _fetch_with_playwright(url, proxy_config=pw_proxy)
    if html and not _looks_like_challenge(html):
        return html

    # 3) Even the browser couldn't get through on this IP — trip the breaker so
    #    the remaining pages of this crawl fail fast (until the next IP rotation
    #    resets it). This is the ONLY place the breaker trips now.
    if not _HARD_BLOCKED:
        logger.warning("Hotfrog blocked on current IP (browser failed too) — "
                       "fail-fast until next IP rotation")
    _HARD_BLOCKED = True
    return None


# ── Search + Detail Parsing ──────────────────────────────────────────────────

def _parse_businesses(html: str, url: str) -> list:
    """Extract business records from a search page HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # 1) JSON-LD path (preferred)
    results = []
    for obj in _iter_jsonld(soup):
        if _is_business(obj):
            d = _norm_business(obj)
            # Only include if we got at least a name
            if d.get("name"):
                results.append(d)

    # 2) CSS selector fallback
    if not results:
        card_selectors = _SELECTORS["card"]
        cards = []
        for sel in card_selectors:
            cards = soup.select(sel)
            if cards:
                break

        for card in cards:
            # Extract Hotfrog profile link
            link_sel = _SELECTORS["card_link"]
            link_el = None
            for sel in link_sel:
                link_el = card.select_one(sel)
                if link_el:
                    break

            name = link_el.get_text(strip=True) if link_el else None
            if not name:
                continue

            href = link_el.get("href") if link_el else None
            if href and href.startswith("/"):
                href = BASE + href

            # Phone
            phone_sel = _SELECTORS["card_phone"]
            phone = None
            for sel in phone_sel:
                el = card.select_one(sel)
                if el:
                    phone = el.get_text(strip=True)
                    break

            # Address — try multiple fallback strategies
            address = None
            for sel in _SELECTORS["card_address"]:
                for el in card.select(sel):
                    txt = el.get_text(strip=True)
                    if not txt:
                        continue
                    if any(p in txt for p in ["Is this your", "Claim this", "Sponsored", "Ad"]):
                        continue
                    address = txt
                    break
                if address:
                    break

            results.append({
                "name": name,
                "address": address,
                "phone": phone,
                "website": None,
                "hotfrog_url": href,
                "description": None,
                "social_facebook": None,
                "social_instagram": None,
                "social_twitter": None,
                "social_linkedin": None,
            })

    # Post-process results: normalize and sanitize
    for r in results:
        r["source_search_url"] = url
        # Ensure hotfrog_url is correctly set
        if not r.get("hotfrog_url"):
            r["hotfrog_url"] = url  # fallback to search URL

        # Sanitise website — filter out social/directory domains
        site = r.get("website") or ""
        if any(d in site.lower() for d in _SOCIAL_DOMAINS):
            r["website"] = None

    return results


def _parse_details_page(html: str, profile_url: str) -> dict:
    """
    Parse a Hotfrog business details page.
    Returns comprehensive business details including social links.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    details = {
        "name": None,
        "address": None,
        "phone": None,
        "website": None,
        "description": None,
        "social_facebook": None,
        "social_instagram": None,
        "social_twitter": None,
        "social_linkedin": None,
        "hotfrog_url": profile_url,
    }

    # 1) Parse JSON-LD
    for obj in _iter_jsonld(soup):
        if _is_business(obj):
            d = _norm_business(obj)
            for k, v in d.items():
                if v is not None:
                    details[k] = v
            break  # Use first business found in JSON-LD

    # 2) Fallback/Supplemental CSS parsing
    if not details["name"]:
        details["name"] = _first_text(soup, *_SELECTORS["detail_name"])

    if not details["description"]:
        desc_text = _first_text(soup, *_SELECTORS["detail_description"])
        if desc_text:
            details["description"] = desc_text
        else:
            # Try meta description
            meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
            if meta and meta.get("content"):
                details["description"] = meta["content"].strip()

    # 3) Parse definition list for website, social, phone, address
    for dt in soup.select("dl.row dt, dl.dl-horizontal dt, .business-details dt"):
        label = dt.get_text(strip=True).lower()
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue

        if "website" in label:
            web_a = dd.find("a")
            if web_a:
                href = web_a.get("href", "")
                if href and not any(d in href.lower() for d in _SOCIAL_DOMAINS):
                    details["website"] = href

        elif "social" in label or "media" in label:
            for a in dd.find_all("a"):
                href = a.get("href", "").lower()
                if "facebook.com" in href:
                    details["social_facebook"] = a.get("href")
                elif "instagram.com" in href:
                    details["social_instagram"] = a.get("href")
                elif "twitter.com" in href or "x.com" in href:
                    details["social_twitter"] = a.get("href")
                elif "linkedin.com" in href:
                    details["social_linkedin"] = a.get("href")

        elif "phone" in label and not details["phone"]:
            details["phone"] = dd.get_text(strip=True)

        elif "address" in label and not details["address"]:
            details["address"] = dd.get_text(strip=True)

    # 4) Fallback: scan all links for social URLs
    if not any([details["social_facebook"], details["social_instagram"],
                details["social_twitter"], details["social_linkedin"]]):
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "facebook.com" in href and not details["social_facebook"]:
                details["social_facebook"] = a["href"]
            elif "instagram.com" in href and not details["social_instagram"]:
                details["social_instagram"] = a["href"]
            elif "twitter.com" in href and not details["social_twitter"]:
                details["social_twitter"] = a["href"]
            elif "linkedin.com" in href and not details["social_linkedin"]:
                details["social_linkedin"] = a["href"]

    return details


# ── Public API ────────────────────────────────────────────────────────────────

def search_businesses(query: str, country: str = "us",
                      limit: int = 10, page: int = 1,
                      rotate_proxy: bool = False) -> list:
    """
    Search Hotfrog for businesses matching a query.

    Args:
        query: Search query (e.g., "plumber new york").
        country: Two-letter country code (default: "us").
        limit: Max results to return (default: 10).
        page: Page number (default: 1).
        rotate_proxy: If True, rotate Tor IP before fetching.

    Returns:
        List of business dicts. Also returns pagination info.

    Pagination parsing:
        The function returns an augmented list with a `_pagination` attribute
        containing `total_pages` (the only field callers use to stop the crawl).
    """
    slug = query.strip().replace(" ", "-").lower()
    url = f"{BASE}/search/{country.lower()}/{slug}"
    if page > 1:
        # Hotfrog paginates via a PATH suffix (/2, /3, …), NOT a `?page=` query
        # param — the query param is silently ignored and re-serves page 1.
        url = f"{url}/{page}"

    html = _fetch_page(url, rotate_proxy=rotate_proxy)
    if not html:
        logger.error("Could not fetch Hotfrog search page: %s", url)
        return []

    results = _parse_businesses(html, url)
    pagination = _parse_pagination(html)

    # Return a list subclass so callers can read `._pagination`. A plain list
    # cannot carry attributes (raises AttributeError), and slicing a plain list
    # would drop it anyway — so wrap the sliced results explicitly.
    out = _ResultList(results[:limit])
    out._pagination = pagination
    return out


def get_business_details(url: str, rotate_proxy: bool = False) -> dict:
    """
    Fetch detailed information for a specific Hotfrog business listing.

    Args:
        url: The full Hotfrog profile URL.
        rotate_proxy: If True, rotate Tor IP before fetching.

    Returns:
        Dict with: name, address, phone, website, description,
        social_facebook, social_instagram, social_twitter, social_linkedin,
        hotfrog_url.
    """
    if not url.startswith(BASE):
        return {"error": "URL must be a hotfrog.com listing", "url": url}

    html = _fetch_page(url, rotate_proxy=rotate_proxy)
    if not html:
        return {"error": "Failed to fetch page", "url": url}

    details = _parse_details_page(html, url)
    details["url"] = url
    return details

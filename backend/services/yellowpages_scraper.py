"""
services/yellowpages_scraper.py — YellowPages.com as a lead source.

YellowPages exposes fairly structured result cards, so we use httpx + BeautifulSoup
(with proxy rotation + Playwright fallback). Returns canonical lead dicts.

Fails soft: any error returns the results gathered so far (possibly empty) so a
blocked source never aborts the whole pipeline.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional
from urllib.parse import quote_plus, urljoin

logger = logging.getLogger(__name__)

BASE = "https://www.yellowpages.com"
TIMEOUT = 4  # YP CDN-blocks scraping (Cloudflare 403/522); fail fast rather than hang

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


import itertools as _it
_circuit_seq = _it.count()  # per-fetch tag → fresh isolated Tor circuit each try


def _proxy_kwargs() -> dict:
    # httpx >= 0.28 takes a single `proxy="scheme://host:port"` string
    # (the old `proxies={...}` dict was removed).
    try:
        from backend.services.proxy_rotator import get_rotator
        proxy = get_rotator().get_proxy(for_playwright=False, tag=f"yp{next(_circuit_seq)}")
        if proxy:
            url = proxy.get("all://") or next(iter(proxy.values()), None)
            if url:
                return {"proxy": url}
    except Exception:
        pass
    return {}


_PROXY_RETRIES = 2  # fresh-circuit retries before falling back to a real browser


def _classify(status: int, text: str) -> str:
    """Bucket an outcome so the retry loop knows if a fresh exit IP helps. YP
    serves a short interstitial (not a 403) when it blocks, so a thin body on an
    otherwise-OK status counts as a block worth retrying on a new circuit."""
    if status < 400 and len(text) > 2000:
        return "ok"
    if status in (401, 403, 429) or (status < 400 and len(text) <= 2000):
        return "blocked"       # burned exit IP or interstitial → fresh circuit
    if status >= 500:
        return "target_down"   # site/CDN issue → a new circuit won't help
    return "reject"            # 404/410 etc → the target says no


def _fetch(url: str) -> Optional[str]:
    import httpx
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def _try(pk: dict):
        try:
            with httpx.Client(headers=headers, timeout=TIMEOUT,
                              follow_redirects=True, **pk) as client:
                r = client.get(url)
            outcome = _classify(r.status_code, r.text)
            return (r.text if outcome == "ok" else None), outcome
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ProxyError,
                httpx.RemoteProtocolError):
            return None, "dead"      # dead/slow circuit → retry on a fresh one
        except Exception as e:
            logger.info("YellowPages httpx fetch failed for %s (%s)", url, e)
            return None, "error"

    # 1) Direct — directories blocklist Tor exits, so the clean IP is best.
    html, outcome = _try({})
    if outcome == "ok":
        return html

    # 2) Proxy — retry on a FRESH circuit for block/dead outcomes; bail fast on
    #    target_down/reject (rotating exit IPs can't fix the target itself).
    for _ in range(_PROXY_RETRIES):
        pk = _proxy_kwargs()          # a fresh isolated circuit each call
        if not pk:
            break                      # no Tor → nothing to retry through
        html, outcome = _try(pk)
        if outcome == "ok":
            return html
        if outcome in ("target_down", "reject"):
            break
        logger.info("YellowPages proxy %s for %s — trying a new circuit", outcome, url)

    # 3) Real browser as last resort.
    return _fetch_playwright(url)


def _fetch_playwright(url: str) -> Optional[str]:
    import asyncio
    import os

    async def _run():
        from playwright.async_api import async_playwright
        headless = os.getenv("SCRAPER_HEADLESS", "true").lower() in ("true", "1", "yes")
        # Directories block proxy/Tor exits — use a direct browser connection.
        async with async_playwright() as p:
            kw = {"headless": headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            browser = await p.chromium.launch(**kw)
            try:
                ctx = await browser.new_context(user_agent=random.choice(_USER_AGENTS))
                page = await ctx.new_page()
                await page.goto(url, timeout=12000, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(1.0, 2.0))
                return await page.content()
            finally:
                await browser.close()

    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception as e:
        logger.warning("YellowPages Playwright fetch failed: %s", e)
        return None


def _parse(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    cards = soup.select("div.result, div.organic div.srp-listing, .search-results .result")
    for card in cards:
        name_el = (card.select_one("a.business-name")
                   or card.select_one(".business-name")
                   or card.select_one("a[class*='business-name']"))
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name:
            continue

        href = name_el.get("href")
        profile_url = urljoin(BASE, href) if href else None

        phone = None
        phone_el = card.select_one(".phones.phone.primary, .phone, [class*='phone']")
        if phone_el:
            phone = phone_el.get_text(strip=True)

        street = card.select_one(".street-address")
        locality = card.select_one(".locality")
        address = ", ".join(
            x.get_text(strip=True) for x in (street, locality) if x and x.get_text(strip=True)
        ) or None

        website = None
        web_el = card.select_one("a.track-visit-website, a[class*='website']")
        if web_el and web_el.get("href"):
            website = web_el.get("href")

        categories = card.select_one(".categories")
        category = categories.get_text(" ", strip=True) if categories else None

        rating = None
        rating_el = card.select_one("[class*='result-rating']")
        if rating_el and rating_el.get("class"):
            # YP encodes rating in class names like "one half" etc — best-effort
            cls = " ".join(rating_el.get("class"))
            rating = _rating_from_class(cls)

        out.append({
            "name": name,
            "address": address,
            "phone": phone,
            "website": website,
            "category": category,
            "description": None,
            "google_rating": rating,
            "source": "yellowpages",
            "source_url": profile_url,
        })
    return out


def _rating_from_class(cls: str) -> Optional[float]:
    words = cls.lower()
    base = 0.0
    nums = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    for w, n in nums.items():
        if f" {w} " in f" {words} " or words.endswith(w) or words.startswith(w):
            base = float(n)
            break
    if "half" in words:
        base += 0.5
    return base or None


def search_yellowpages(query: str, location: str, limit: int = 50,
                       max_pages: int = 5) -> list[dict]:
    """Search YellowPages for businesses. Returns up to `limit` canonical dicts."""
    results: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        if len(results) >= limit:
            break
        url = (f"{BASE}/search?search_terms={quote_plus(query)}"
               f"&geo_location_terms={quote_plus(location)}")
        if page > 1:
            url += f"&page={page}"
        html = _fetch(url)
        if not html:
            break
        batch = _parse(html)
        if not batch:
            break
        new = 0
        for r in batch:
            key = (r["name"].lower(), (r.get("phone") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            results.append(r)
            new += 1
            if len(results) >= limit:
                break
        if new == 0:
            break
        time.sleep(random.uniform(1.0, 2.0))
    logger.info("YellowPages source returned %d businesses for '%s in %s'",
                len(results), query, location)
    return results[:limit]

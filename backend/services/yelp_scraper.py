"""
services/yelp_scraper.py — Yelp as a lead source.

Yelp aggressively obfuscates markup and rate-limits scraping, so we:
  1. Prefer JSON-LD (`<script type="application/ld+json">`) which Yelp still
     emits with itemListElement business data.
  2. Fall back to parsing result cards by stable-ish attributes.
  3. Use proxy rotation + Playwright fallback.

Yield is expected to be lower/less reliable than Maps or YellowPages — this is a
*supplementary* source. Fails soft (returns whatever it gathered).
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Optional
from urllib.parse import quote_plus, urljoin

logger = logging.getLogger(__name__)

BASE = "https://www.yelp.com"
TIMEOUT = 4  # Yelp hard-blocks scraping (PerimeterX); fail fast rather than hang

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
        proxy = get_rotator().get_proxy(for_playwright=False, tag=f"yelp{next(_circuit_seq)}")
        if proxy:
            url = proxy.get("all://") or next(iter(proxy.values()), None)
            if url:
                return {"proxy": url}
    except Exception:
        pass
    return {}


_PROXY_RETRIES = 2  # fresh-circuit retries before falling back to a real browser


def _classify(status: int, blocked: bool) -> str:
    """Bucket an HTTP outcome so the retry loop knows whether a fresh exit IP
    can help. A block burns the current IP (retry elsewhere); a 5xx or a hard
    4xx is the target's problem (rotating won't fix it)."""
    if status < 400 and not blocked:
        return "ok"
    if blocked or status in (401, 403, 429):
        return "blocked"       # this exit IP is burned → fresh circuit
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
            outcome = _classify(r.status_code, _is_blocked(r.text))
            return (r.text if outcome == "ok" else None), outcome
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ProxyError,
                httpx.RemoteProtocolError):
            return None, "dead"      # dead/slow circuit → retry on a fresh one
        except Exception as e:
            logger.info("Yelp httpx fetch failed for %s (%s)", url, e)
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
        logger.info("Yelp proxy %s for %s — trying a new circuit", outcome, url)

    # 3) Real browser as last resort.
    html = _fetch_playwright(url)
    if html and not _is_blocked(html):
        return html
    return None


def _is_blocked(html: Optional[str]) -> bool:
    """Detect Yelp's anti-bot block/captcha page."""
    if not html or len(html) < 2000:
        return True
    low = html.lower()
    return any(s in low for s in ("captcha", "unusual traffic", "are you a human",
                                  "access to this page has been denied",
                                  "px-captcha", "perimeterx"))


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
        logger.warning("Yelp Playwright fetch failed: %s", e)
        return None


def _parse_jsonld(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
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
            # itemList of businesses
            elements = item.get("itemListElement") if isinstance(item, dict) else None
            if elements:
                for e in elements:
                    biz = e.get("item", e) if isinstance(e, dict) else None
                    rec = _norm_jsonld(biz)
                    if rec:
                        out.append(rec)
            elif isinstance(item, dict) and _is_business(item):
                rec = _norm_jsonld(item)
                if rec:
                    out.append(rec)
    return out


def _is_business(obj: dict) -> bool:
    t = obj.get("@type", "")
    types = t if isinstance(t, list) else [t]
    return any("Business" in str(x) or x in (
        "Restaurant", "LocalBusiness", "Organization", "Store") for x in types)


def _norm_jsonld(obj) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if not name:
        return None
    addr = obj.get("address")
    if isinstance(addr, dict):
        addr = ", ".join(filter(None, [
            addr.get("streetAddress"), addr.get("addressLocality"),
            addr.get("addressRegion"), addr.get("postalCode"),
        ]))
    rating = None
    agg = obj.get("aggregateRating")
    reviews = 0
    if isinstance(agg, dict):
        try:
            rating = float(agg.get("ratingValue"))
        except (TypeError, ValueError):
            pass
        try:
            reviews = int(agg.get("reviewCount") or agg.get("ratingCount") or 0)
        except (TypeError, ValueError):
            reviews = 0
    return {
        "name": name,
        "address": addr or None,
        "phone": obj.get("telephone"),
        "website": None,  # Yelp hides external site behind a redirect
        "category": _first(obj.get("servesCuisine")) or None,
        "description": obj.get("description"),
        "google_rating": rating,
        "google_review_count": reviews,
        "source": "yelp",
        "source_url": obj.get("url"),
    }


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def search_yelp(query: str, location: str, limit: int = 50,
                max_pages: int = 5) -> list[dict]:
    """Search Yelp for businesses. Returns up to `limit` canonical dicts."""
    results: list[dict] = []
    seen: set[str] = set()
    for page in range(max_pages):
        if len(results) >= limit:
            break
        start = page * 10
        url = (f"{BASE}/search?find_desc={quote_plus(query)}"
               f"&find_loc={quote_plus(location)}")
        if start:
            url += f"&start={start}"
        html = _fetch(url)
        if not html:
            break
        batch = _parse_jsonld(html)
        if not batch:
            break
        new = 0
        for r in batch:
            if r.get("source_url"):
                r["source_url"] = urljoin(BASE, r["source_url"])
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
        time.sleep(random.uniform(1.5, 2.5))
    logger.info("Yelp source returned %d businesses for '%s in %s'",
                len(results), query, location)
    return results[:limit]

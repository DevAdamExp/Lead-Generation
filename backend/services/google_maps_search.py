"""
services/google_maps_search.py — Google Maps as a PRIMARY lead source.

Unlike google_maps_scraper.py (which only enriches an existing lead with a
rating), this module *discovers* businesses by searching Google Maps, scrolling
the results feed to load many listings, then opening each result's detail panel
to extract the richest possible data per business:

    name, category, full address, phone, website, rating, review_count,
    hours (open/closed + weekly), price_level, plus_code, lat/lng,
    editorial description ("about"), and the canonical Maps URL.

Design goals:
  - VOLUME: scroll the feed until `limit` unique results (or feed end).
  - DEPTH: open each place to read the detail panel, not just the card.
  - ROBUSTNESS: every field has multiple selector strategies + aria-label
    fallbacks, mirroring the defensive style used elsewhere in this codebase.
  - POLITENESS: randomised human-like delays; one shared browser/context.
  - HEADLESS by default (SCRAPER_HEADLESS) so no window steals focus.

Returns a list of canonical lead dicts (see services/sources.py LEAD_FIELDS).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Callable, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

_DEBUG_DIR = "/tmp/maps_debug"
os.makedirs(_DEBUG_DIR, exist_ok=True)

# ── Selectors (multiple strategies per field, tried in order) ────────────────
_FEED_SELECTORS = [
    "div[role='feed']",
    "div[aria-label*='Results']",
    "div.m6QErb[aria-label]",
]

_CARD_SELECTORS = [
    "div[role='feed'] > div > div[jsaction]",
    "a.hfpxzc",                       # place links inside the feed
    "div.Nv2PK",                      # classic result card
]

# Only the place-title h1 — NOT generic <h1> (the results pane h1 says "Results").
_DETAIL_NAME = [
    "h1.DUwDvf",
    "h1[class*='DUwDvf']",
    "div[role='main'] h1.DUwDvf",
]

# Names that are actually UI chrome, never a business.
_NAME_BLOCKLIST = {"results", "sponsored", "directions", "google maps"}

_DETAIL_CATEGORY = [
    "button[jsaction*='category']",
    ".DkEaL",
    "button.DkEaL",
]

_RATING_SELECTORS = [
    "div.F7nice span[aria-hidden='true']",
    "div.F7nice span.ceNzKf",
    "span.MW4etd",
]

_REVIEW_SELECTORS = [
    "div.F7nice span[aria-label*='review']",
    "button[aria-label*='review']",
    "span.UY7F9",
]

_ABOUT_SELECTORS = [
    "div.PYvSYb",                     # editorial summary
    "div.WeS02d div.PbZDve",
    "div[class*='editorial']",
]

_END_OF_LIST_RE = re.compile(
    r"you'?ve reached the end|no more results|end of (the )?list", re.IGNORECASE
)


def _headless() -> bool:
    return os.getenv("SCRAPER_HEADLESS", "true").lower() in ("true", "1", "yes")


async def _text_from(page_or_el, selectors: list[str]) -> Optional[str]:
    """Return inner_text of the first selector that matches and has content."""
    for sel in selectors:
        try:
            el = page_or_el.locator(sel).first
            if await el.count() > 0:
                txt = (await el.inner_text()).strip()
                if txt:
                    return txt
        except Exception:
            continue
    return None


def _parse_latlng(url: str) -> tuple[Optional[float], Optional[float]]:
    """Extract lat/lng from a Google Maps URL (…/@lat,lng,zoom…)."""
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url or "")
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    # !3dLAT!4dLNG form (present on place URLs)
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url or "")
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    return None, None


async def _extract_data_items(page) -> dict:
    """
    Google Maps detail panel exposes structured rows as
    `button[data-item-id="…"]` / `a[data-item-id="…"]` with an aria-label and a
    `.Io6YTe` text node. This is the most stable way to read address/phone/
    website/plus-code regardless of cosmetic class churn.
    """
    out: dict = {}
    try:
        items = page.locator("[data-item-id]")
        count = await items.count()
        for i in range(min(count, 30)):
            el = items.nth(i)
            try:
                item_id = (await el.get_attribute("data-item-id")) or ""
                aria = (await el.get_attribute("aria-label")) or ""
                # Prefer the visible text node when present
                text = ""
                try:
                    inner = el.locator(".Io6YTe").first
                    if await inner.count() > 0:
                        text = (await inner.inner_text()).strip()
                except Exception:
                    pass
                value = text or aria

                if item_id == "address":
                    out["address"] = _strip_label(value, ("Address:",))
                elif item_id.startswith("phone"):
                    out["phone"] = _strip_label(value, ("Phone:", "Phone number:"))
                elif item_id == "authority":
                    href = await el.get_attribute("href")
                    out["website"] = href or value
                elif item_id == "oloc":
                    out["plus_code"] = _strip_label(value, ("Plus code:",))
                elif item_id.startswith("merchant") or "hours" in item_id:
                    out.setdefault("hours_raw", value)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _strip_label(value: str, labels: tuple[str, ...]) -> str:
    v = value.strip()
    for lbl in labels:
        if v.lower().startswith(lbl.lower()):
            return v[len(lbl):].strip()
    return v


async def _extract_hours(page) -> Optional[str]:
    """Read the open/closed status + weekly hours if exposed."""
    for sel in ["div.t39EBf", "div[aria-label*='Hours']", "div.OqCZI"]:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                label = await el.get_attribute("aria-label")
                if label:
                    return label.strip()
        except Exception:
            continue
    return None


def _parse_rating(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"(\d+[.,]\d+|\d+)", text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


def _parse_reviews(text: Optional[str]) -> int:
    if not text:
        return 0
    m = re.search(r"([\d,]+)", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return 0
    return 0


async def _panel(page):
    """
    Scope locator for the OPEN place detail panel. Critical: the results feed
    (with its own ratings / data rows) stays in the DOM, so we must restrict
    extraction to the panel container or we read a neighbouring card's data.
    """
    for sel in ["div[role='main']:has(h1.DUwDvf)", "div[role='main']"]:
        loc = page.locator(sel).last
        try:
            if await loc.count() > 0:
                return loc
        except Exception:
            continue
    return page


async def _extract_detail(page, fallback_name: str = "") -> dict:
    """Extract the full record from an open detail panel."""
    panel = await _panel(page)

    name = await _text_from(panel, _DETAIL_NAME) or fallback_name
    if name and name.strip().lower() in _NAME_BLOCKLIST:
        name = fallback_name
    category = await _text_from(panel, _DETAIL_CATEGORY)

    # Rating + reviews both live in div.F7nice as "4.6\n(1,580)".
    rating = None
    reviews = 0
    f7 = await _text_from(panel, ["div.F7nice"])
    if f7:
        rating = _parse_rating(f7)
        mm = re.search(r"\(([\d,]+)\)", f7)
        if mm:
            reviews = _parse_reviews(mm.group(1))
    if rating is None:
        rating = _parse_rating(await _text_from(panel, _RATING_SELECTORS))
    if not reviews:
        reviews = _parse_reviews(await _text_from(panel, _REVIEW_SELECTORS))
    about = await _text_from(panel, _ABOUT_SELECTORS)

    items = await _extract_data_items(panel)
    hours = await _extract_hours(panel)
    lat, lng = _parse_latlng(page.url)

    # price level (e.g. "$$") often sits near the category
    price_level = None
    try:
        pl = panel.locator("span[aria-label*='Price']").first
        if await pl.count() > 0:
            price_level = (await pl.get_attribute("aria-label")) or None
    except Exception:
        pass

    return {
        "name": name,
        "category": category,
        "address": items.get("address"),
        "phone": items.get("phone"),
        "website": items.get("website"),
        "plus_code": items.get("plus_code"),
        "hours": hours or items.get("hours_raw"),
        "price_level": price_level,
        "description": about,
        "google_rating": rating,
        "google_review_count": reviews,
        "google_is_open": (None if not hours else ("closed" not in hours.lower())),
        "google_maps_url": page.url,
        "latitude": lat,
        "longitude": lng,
        "has_google_maps": True,
        "source": "google_maps",
    }


async def _scroll_feed(page, feed, target: int, max_rounds: int = 60) -> int:
    """Scroll the results feed until we have >= target cards or it truly ends.

    Google Maps lazy-loads ~20 cards at a time and only fetches more when the
    LAST card scrolls into view — scrolling the container to a static bottom
    doesn't reliably trigger it, which is why the feed used to stall near ~22.
    We scroll the last card into view each round and tolerate transient stalls
    (the network fetch can take a few seconds) before giving up.
    """
    last_count = 0
    stagnant = 0
    for round_idx in range(max_rounds):
        try:
            cards = await _count_cards(page)
        except Exception:
            cards = last_count

        if cards >= target:
            return cards

        # Detect "end of list" sentinel
        try:
            body = (await feed.inner_text())[-500:]
            if _END_OF_LIST_RE.search(body):
                logger.info("Maps feed reached end of list at %d cards", cards)
                return cards
        except Exception:
            pass

        last_count = cards

        # Trigger lazy-load: scroll the LAST card into view, then nudge the
        # container bottom. scrollIntoView on the tail card is what Maps watches.
        try:
            await feed.evaluate(
                "el => {"
                " const c = el.querySelectorAll('a.hfpxzc');"
                " if (c.length) c[c.length - 1].scrollIntoView({block: 'end'});"
                " el.scrollTo(0, el.scrollHeight);"
                "}"
            )
        except Exception:
            try:
                await page.mouse.wheel(0, 4000)
            except Exception:
                pass

        # Wait for the count to ACTUALLY grow instead of a blind sleep — Tor's
        # lazy-load round-trip is slow and unpredictable. Only a genuine timeout
        # (no new cards landed) counts as stagnation.
        # ponytail: 4s ceiling per scroll; bump if exit nodes are very slow.
        try:
            await page.wait_for_function(
                "n => document.querySelectorAll('a.hfpxzc').length > n",
                arg=last_count,
                timeout=4000,
            )
            stagnant = 0
        except Exception:
            stagnant += 1
            if stagnant >= 3:
                logger.info("Maps feed stopped growing at %d cards", cards)
                return last_count

    return last_count


async def _count_cards(page) -> int:
    # Count the SAME selector the harvest uses (a.hfpxzc place links). The old
    # _CARD_SELECTORS[0] counted feed chrome divs, over-counting and disagreeing
    # with what we actually collect — which is why scroll stopped early.
    return await page.locator("a.hfpxzc").count()


async def _get_cards(page):
    for sel in _CARD_SELECTORS:
        loc = page.locator(sel)
        if await loc.count() > 0:
            return loc
    return None


async def search_maps_businesses(
    query: str,
    location: str,
    country: str = "us",
    limit: int = 50,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:
    """
    Discover businesses on Google Maps for `query` in `location`.

    Returns a list of canonical lead dicts (up to `limit`).
    """
    from playwright.async_api import async_playwright
    try:
        from playwright_stealth import Stealth
        _stealth = True
    except ImportError:
        _stealth = False

    # Per-context Tor circuits (same design as google_maps_scraper's enrichment
    # batch): the feed scrape and every detail worker ride their OWN exit IP, so
    # opening N place panels at once doesn't stack N requests on a single IP.
    # Chromium honours a per-context proxy only if launched with one, hence the
    # "per-context" sentinel.
    _rotator = None
    use_proxy = False
    try:
        from backend.services.proxy_rotator import get_rotator
        _rotator = get_rotator()
        use_proxy = _rotator.get_proxy(for_playwright=True, tag="disc0") is not None
    except Exception:
        _rotator = None

    def _ctx_proxy(tag: str):
        if not (use_proxy and _rotator):
            return None
        try:
            return _rotator.get_proxy(for_playwright=True, tag=tag)
        except Exception:
            return None

    try:
        from backend.config import settings as _settings
        want = max(1, int(getattr(_settings, "MAPS_DETAIL_CONCURRENCY", 4)))
        delay_min = float(getattr(_settings, "MAPS_ENRICH_DELAY_MIN", 1.5))
        delay_max = float(getattr(_settings, "MAPS_ENRICH_DELAY_MAX", 3.0))
    except Exception:
        want, delay_min, delay_max = 4, 1.5, 3.0

    search_term = f"{query} in {location}".strip()
    url = f"https://www.google.com/maps/search/{quote_plus(search_term)}?hl=en"

    results: list[dict] = []
    seen_urls: set[str] = set()
    seen_names: set[str] = set()

    async def _open(browser, tag: str):
        """A fresh context (own circuit) + page, stealthed."""
        ctx_kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 1000},
            locale="en-US",
        )
        wp = _ctx_proxy(tag)
        if wp:
            ctx_kwargs["proxy"] = wp
        context = await browser.new_context(**ctx_kwargs)
        pg = await context.new_page()
        if _stealth:
            try:
                await Stealth().apply_stealth_async(pg)
            except Exception:
                pass
        return context, pg

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": _headless(),
            "args": [
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1400,1000",
            ],
        }
        if use_proxy:
            launch_kwargs["proxy"] = {"server": "per-context"}

        browser = await p.chromium.launch(**launch_kwargs)
        context, page = await _open(browser, "disc0")

        try:
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.5, 4.0))
            await _maybe_accept_consent(page)

            # If Maps deep-linked straight to a single place, extract it directly.
            feed = await _wait_for_feed(page)
            if feed is None:
                single = await _extract_detail(page, fallback_name=query)
                if single.get("name"):
                    results.append(single)
                logger.info("Maps returned a single place (no feed) for '%s'", search_term)
                return results[:limit]

            await _scroll_feed(page, feed, target=limit)

            # Collect canonical place URLs from the feed up front, then navigate
            # to each directly. Navigating (vs clicking) guarantees a fresh,
            # fully-loaded panel every time — clicking leaves stale panel state
            # from the previous card and corrupts extraction.
            links = page.locator("div[role='feed'] a.hfpxzc")
            n = await links.count()
            if n == 0:
                links = page.locator("a.hfpxzc")
                n = await links.count()
            place_urls: list[tuple[str, str]] = []
            for i in range(n):
                try:
                    href = await links.nth(i).get_attribute("href")
                    aria = (await links.nth(i).get_attribute("aria-label")) or ""
                    if href and "/maps/place/" in href and href not in seen_urls:
                        seen_urls.add(href)
                        place_urls.append((href, aria))
                except Exception:
                    continue
            logger.info("Maps feed yielded %d place URLs for '%s'", len(place_urls), search_term)

            # Detail extraction, N-wide. Was a sequential goto-per-place loop on a
            # single page — with ~60 places at ~5s each that was ~5 min PER TILE and
            # the largest single cost in the pipeline. Workers pull from a shared
            # queue; asyncio doesn't preempt mid-statement, so `results`/`seen_names`
            # need no lock.
            queue: asyncio.Queue = asyncio.Queue()
            for item in place_urls:
                queue.put_nowait(item)
            n_ctx = min(want,
                        max(2, _rotator.circuit_capacity() if (use_proxy and _rotator) else 1),
                        max(1, len(place_urls)))

            async def _detail_worker(worker_id: int):
                # Worker 0 reuses the feed page's context (already warm + has a
                # circuit); the rest get their own.
                if worker_id == 0:
                    ctx, pg = None, page
                else:
                    ctx, pg = await _open(browser, f"disc{worker_id}")
                try:
                    while len(results) < limit:
                        try:
                            href, aria = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        try:
                            await pg.goto(href, timeout=40000, wait_until="domcontentloaded")
                            try:
                                await pg.locator("h1.DUwDvf").first.wait_for(
                                    state="visible", timeout=9000)
                            except Exception:
                                pass
                            try:
                                # 2.5s, not 6s: these rows are a bonus, and a
                                # service-area business genuinely has no address
                                # row — the old 6s was a guaranteed dead stall on
                                # every such place.
                                await pg.locator(
                                    "[data-item-id='address'], button[data-item-id^='phone'], "
                                    "[data-item-id='authority'], button.DkEaL"
                                ).first.wait_for(state="visible", timeout=2500)
                            except Exception:
                                pass
                            await asyncio.sleep(random.uniform(0.6, 1.2))

                            record = await _extract_detail(pg, fallback_name=aria)
                            name = (record.get("name") or "").strip()
                            if not name or name.lower() in _NAME_BLOCKLIST:
                                continue
                            dedup_key = name.lower()
                            if dedup_key in seen_names:
                                continue
                            seen_names.add(dedup_key)
                            results.append(record)
                            if on_progress:
                                try:
                                    on_progress(len(results), limit, name)
                                except Exception:
                                    pass
                        except Exception as exc:
                            logger.debug("Maps place extraction failed for %s: %s", href, exc)
                        # Polite per-worker pacing (each worker is its own exit IP).
                        await asyncio.sleep(random.uniform(delay_min, delay_max))
                finally:
                    if ctx is not None:
                        await ctx.close()

            await asyncio.gather(*[_detail_worker(w) for w in range(n_ctx)])

        except Exception as exc:
            logger.warning("Google Maps search failed for '%s': %s", search_term, exc)
            await _capture(page, search_term)
        finally:
            await browser.close()

    logger.info("Google Maps source returned %d businesses for '%s'", len(results), search_term)
    return results[:limit]


async def _wait_for_feed(page):
    """Wait for the results feed; return the feed locator or None if single-place."""
    for _ in range(10):
        for sel in _FEED_SELECTORS:
            loc = page.locator(sel).first
            try:
                if await loc.count() > 0:
                    return loc
            except Exception:
                continue
        # Detail page (single place) shows an h1 but no feed
        try:
            if await page.locator("h1.DUwDvf").count() > 0:
                return None
        except Exception:
            pass
        await asyncio.sleep(1.0)
    return None


async def _maybe_accept_consent(page) -> None:
    """Dismiss Google's cookie/consent interstitial if present."""
    selectors = [
        "button[aria-label*='Accept all']",
        "button[aria-label*='Reject all']",
        "form[action*='consent'] button",
        "button:has-text('Accept all')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click(timeout=3000)
                await asyncio.sleep(1.5)
                return
        except Exception:
            continue


async def _capture(page, label: str) -> None:
    try:
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", label)[:40]
        await page.screenshot(path=os.path.join(_DEBUG_DIR, f"maps_search_{safe}.png"))
    except Exception:
        pass


def search_maps_businesses_sync(
    query: str, location: str, country: str = "us", limit: int = 50, on_progress=None
) -> list[dict]:
    """Sync wrapper for Celery workers."""
    return asyncio.run(
        search_maps_businesses(query, location, country, limit, on_progress)
    )

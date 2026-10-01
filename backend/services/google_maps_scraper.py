"""
services/google_maps_scraper.py — Google Maps ENRICHMENT (rating/reviews/open).

Given a business we already know (name + city, discovered via YellowPages/Yelp/
Hotfrog), confirm it on Google Maps and attach its rating, review count and
open-status.

Accuracy design (why this file is now thin):
  Extraction is DELEGATED to google_maps_search._extract_detail — the discovery
  module's battle-tested reader that (a) scopes to the OPEN place panel so it can
  never read a neighbouring feed card's number, and (b) prefers Google's stable
  `data-item-id` semantic rows over cosmetic CSS classes. This module only has to
  land on the RIGHT place page and hand it to that reader:

    1. Search "https://www.google.com/maps/search/{name} {city}".
    2. If a results feed appears, pick the feed link whose name best matches the
       target and NAVIGATE to it (navigate, not click → a fresh, correct panel).
       If Maps deep-linked straight to one place, use it as-is.
    3. Extract via _extract_detail, then VERIFY the panel's name fuzzy-matches the
       target. On a mismatch (or no results) attach NOTHING (_EMPTY) rather than a
       wrong business's rating — silently-wrong data is worse than missing data.

  Per-lead failure isolation, a per-batch dedup cache, and per-context Tor
  circuits (one exit IP per enrichment worker) live in scrape_maps_ratings_batch.
"""
import asyncio
import logging
import os
import random
import re
from typing import Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# Debug directory for failure screenshots
_DEBUG_DIR = "/tmp/maps_debug"
os.makedirs(_DEBUG_DIR, exist_ok=True)

_EMPTY = {
    "has_google_maps": False,
    "google_rating": None,
    "google_review_count": 0,
    "google_maps_url": None,
    "google_is_open": None,
}


def _normalize_name(name: str) -> str:
    """Normalize a business name for comparison."""
    return re.sub(r"[^a-zA-Z0-9\s]", "", name).lower().strip()


async def _fuzzy_match_names(search_name: str, result_name: str, threshold: float = 0.6) -> bool:
    """
    Check if two business names refer to the same entity.
    Uses token-based fuzzy matching to handle typos, abbreviations, etc.
    """
    try:
        from rapidfuzz import fuzz
        search_norm = _normalize_name(search_name)
        result_norm = _normalize_name(result_name)
        if not search_norm or not result_norm:
            return False
        # Token sort handles word order differences
        ratio = fuzz.token_sort_ratio(search_norm, result_norm) / 100.0
        # Partial ratio handles substring matches
        partial = fuzz.partial_ratio(search_norm, result_norm) / 100.0
        # Token set handles extra/missing words
        token_set = fuzz.token_set_ratio(search_norm, result_norm) / 100.0
        score = max(ratio, partial, token_set)
        if score >= threshold:
            logger.debug("Name match: '%s' ~ '%s' = %.2f", search_name, result_name, score)
            return True
        logger.debug("Name no-match: '%s' vs '%s' = %.2f", search_name, result_name, score)
        return False
    except ImportError:
        return search_name.lower().strip() in result_name.lower().strip()


def _pick_best_place(candidates: list, target: str) -> Optional[str]:
    """From [(href, aria_name), ...] pick the place href whose name best matches
    `target`. Returns the best href (>= 0.4 fuzzy), else the first place href
    (Maps ranks the intended business first for a name+city query), else None.

    Pure (no browser) so the ranking logic is unit-testable. The final say on
    correctness is the name-match gate in scrape_single_lead, not this pick —
    this only decides which panel to open.
    """
    places = [(h, a or "") for (h, a) in candidates if h and "/maps/place/" in h]
    if not places:
        return None
    search_norm = _normalize_name(target)
    try:
        from rapidfuzz import fuzz
        scored = [(fuzz.token_sort_ratio(search_norm, _normalize_name(a)) / 100.0, h)
                  for h, a in places]
    except ImportError:
        scored = [(1.0 if search_norm and search_norm in _normalize_name(a) else 0.0, h)
                  for h, a in places]
    best_score, best_href = max(scored, key=lambda x: x[0])
    return best_href if best_score >= 0.4 else places[0][0]


async def _best_match_href(page, target: str) -> Optional[str]:
    """Collect the feed's place links (href + aria-label name) and pick the best
    match for `target`. Returns a place URL to navigate to, or None."""
    for feed_sel in ("div[role='feed'] a.hfpxzc", "a.hfpxzc"):
        links = page.locator(feed_sel)
        n = await links.count()
        if not n:
            continue
        candidates = []
        for i in range(min(n, 20)):
            try:
                href = await links.nth(i).get_attribute("href")
                aria = (await links.nth(i).get_attribute("aria-label")) or ""
                candidates.append((href, aria))
            except Exception:
                continue
        return _pick_best_place(candidates, target)
    return None


def _cache(dedup_cache: Optional[dict], key: tuple, result: dict) -> dict:
    """Store `result` under `key` (if a cache is in use) and return it."""
    if dedup_cache is not None:
        dedup_cache[key] = result
    return result


async def _capture_failure_screenshot(page, name: str, label: str):
    """Save a screenshot when scraping fails, for offline debugging."""
    try:
        import time
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)[:40]
        ts = int(time.time())
        path = os.path.join(_DEBUG_DIR, f"{safe_name}_{label}_{ts}.png")
        await page.screenshot(path=path, full_page=False)
        logger.info("Saved debug screenshot: %s", path)
    except Exception:
        pass


async def scrape_single_lead(page, item: dict, dedup_cache: Optional[dict] = None) -> dict:
    """
    Confirm one known business on Google Maps and return its rating/reviews/open.
    Returns _EMPTY (never partial/guessed data) when the business can't be
    matched — a mismatch attaches nothing rather than a wrong business's numbers.
    """
    # Reuse the discovery module's panel-scoped, data-item-id-based reader.
    from backend.services.google_maps_search import (
        _extract_detail, _wait_for_feed, _maybe_accept_consent,
    )

    name = item.get("name", "")
    city = item.get("city", "")
    if not name:
        return dict(_EMPTY)

    cache_key = (name.lower().strip(), city.lower().strip())
    if dedup_cache is not None and cache_key in dedup_cache:
        result = dict(dedup_cache[cache_key])
        result["_cached"] = True
        return result

    try:
        query = f"{name} {city}".strip()
        url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        await asyncio.sleep(random.uniform(2.0, 3.5))
        await _maybe_accept_consent(page)

        feed = await _wait_for_feed(page)
        if feed is not None:
            # Multiple results — open the best-matching place's own panel.
            href = await _best_match_href(page, name)
            if not href:
                return _cache(dedup_cache, cache_key, dict(_EMPTY))
            await page.goto(href, timeout=25000, wait_until="domcontentloaded")
            try:
                await page.locator("h1.DUwDvf").first.wait_for(state="visible", timeout=8000)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(0.5, 1.0))
        elif await page.locator("h1.DUwDvf").count() == 0:
            # No feed AND no place panel → genuinely no results.
            return _cache(dedup_cache, cache_key, dict(_EMPTY))
        # else: Maps deep-linked straight to the single matching place — use it.

        record = await _extract_detail(page, fallback_name=name)
        detail_name = (record.get("name") or "").strip()
        if not detail_name or not await _fuzzy_match_names(name, detail_name):
            logger.info("Maps mismatch for '%s' (got '%s') — attaching no data",
                        name, detail_name)
            return _cache(dedup_cache, cache_key, dict(_EMPTY))

        result = {
            "has_google_maps": True,   # confirmed on a matching place page
            "google_rating": record.get("google_rating"),
            "google_review_count": record.get("google_review_count") or 0,
            "google_maps_url": record.get("google_maps_url"),
            "google_is_open": record.get("google_is_open"),
        }
        return _cache(dedup_cache, cache_key, result)

    except Exception as exc:
        logger.warning("Maps scrape failed for '%s': %s", name, exc)
        await _capture_failure_screenshot(page, name, "exception")
        return _cache(dedup_cache, cache_key, dict(_EMPTY))


async def scrape_maps_ratings_batch(
    leads_data: list[dict],
    on_progress=None,
) -> dict:
    """
    Scrapes Google Maps ratings for a list of leads using ONE shared browser.
    Each lead is handled independently — one failure doesn't affect others.

    Args:
        leads_data: [{id, name, city}, ...]
        on_progress: callback(idx, total, name)

    Returns:
        {lead_id: {has_google_maps, google_rating, google_review_count, ...}}
    """
    try:
        from playwright_stealth import Stealth
        _stealth = True
    except ImportError:
        _stealth = False

    import os
    from playwright.async_api import async_playwright

    # Default to headless for Docker compatibility
    headless_env = os.getenv("SCRAPER_HEADLESS", "true").lower()
    headless = headless_env in ("true", "1", "yes")

    # Proxy: give EACH enrichment worker its own isolated circuit (own exit IP)
    # instead of the whole browser sharing one. That's what lets the N contexts
    # run concurrently without Google rate-limiting a single shared IP.
    # Chromium honours a per-context proxy only if launched with a proxy, so we
    # launch with the "per-context" sentinel and set the real circuit per worker.
    _rotator = None
    use_proxy = False
    try:
        from backend.services.proxy_rotator import get_rotator
        _rotator = get_rotator()
        use_proxy = _rotator.get_proxy(for_playwright=True, tag="maps0") is not None
    except Exception:
        _rotator = None

    def _worker_proxy(w: int):
        if not (use_proxy and _rotator):
            return None
        try:
            return _rotator.get_proxy(for_playwright=True, tag=f"maps{w}")
        except Exception:
            return None

    results = {}

    # Per-batch dedup cache to avoid re-scraping the same business
    dedup_cache: dict[tuple[str, str], dict] = {}
    dedup_hits = 0

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": headless,
            "args": [
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--window-size=1280,720",
            ],
        }
        if use_proxy:
            # Sentinel: enables Chromium's proxy path; real circuit set per context.
            launch_kwargs["proxy"] = {"server": "per-context"}
            logger.info("Per-context Tor circuits enabled for Maps enrichment")

        browser = await p.chromium.launch(**launch_kwargs)

        # Parallel enrichment: N independent browser contexts (cheap — a second
        # *browser* is not) each pull leads from a shared queue. With per-context
        # Tor circuits, each context has its OWN exit IP, so Google throttles per
        # IP, not globally — concurrency is safe up to the number of distinct
        # circuits available. We cap n_ctx by that capacity so we never stack
        # several contexts onto one IP (which would raise per-IP rate + blocks).
        try:
            from backend.config import settings as _settings
            want = max(1, int(getattr(_settings, "MAPS_ENRICH_CONCURRENCY", 4)))
            delay_min = float(getattr(_settings, "MAPS_ENRICH_DELAY_MIN", 1.5))
            delay_max = float(getattr(_settings, "MAPS_ENRICH_DELAY_MAX", 3.0))
        except Exception:
            want, delay_min, delay_max = 4, 1.5, 3.0
        # Floor at 2 (the historical default: up to 2 contexts share one IP —
        # an accepted tradeoff), then scale UP to one context per distinct IP.
        cap = _rotator.circuit_capacity() if (use_proxy and _rotator) else 1
        n_ctx = min(want, max(2, cap), max(1, len(leads_data)))

        queue: asyncio.Queue = asyncio.Queue()
        for idx, item in enumerate(leads_data):
            queue.put_nowait((idx, item))

        progress_count = {"n": 0}
        total = len(leads_data)

        async def _worker(worker_id: int):
            # asyncio coroutines don't preempt mid-statement, so the shared
            # dedup_cache / results dicts need no lock.
            nonlocal dedup_hits
            ctx_kwargs = dict(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
                locale="en-US",
            )
            wp = _worker_proxy(worker_id)  # this worker's own isolated circuit
            if wp:
                ctx_kwargs["proxy"] = wp
            context = await browser.new_context(**ctx_kwargs)
            try:
                while True:
                    try:
                        idx, item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    lead_id = item["id"]
                    name = item.get("name", "")
                    cache_key = (name.lower().strip(), item.get("city", "").lower().strip())

                    if cache_key in dedup_cache:
                        dedup_hits += 1
                        results[lead_id] = dict(dedup_cache[cache_key])
                    else:
                        page = await context.new_page()
                        if _stealth:
                            try:
                                await Stealth().apply_stealth_async(page)
                            except Exception:
                                pass
                        try:
                            results[lead_id] = await scrape_single_lead(
                                page, item, dedup_cache=dedup_cache)
                        except Exception as exc:
                            logger.error("Unexpected error for lead '%s': %s", name, exc)
                            results[lead_id] = dict(_EMPTY)
                            dedup_cache[cache_key] = results[lead_id]
                        finally:
                            await page.close()
                        # Polite delay between real queries (per worker).
                        await asyncio.sleep(random.uniform(delay_min, delay_max))

                    progress_count["n"] += 1
                    if on_progress:
                        try:
                            on_progress(progress_count["n"], total, name)
                        except Exception:
                            pass
            finally:
                await context.close()

        await asyncio.gather(*[_worker(w) for w in range(n_ctx)])
        await browser.close()

    if dedup_hits:
        logger.info("Maps dedup cache: %d hits, %d unique leads scraped", dedup_hits, len(dedup_cache))

    return results


def scrape_maps_ratings_batch_sync(leads_data: list[dict], on_progress=None) -> dict:
    """Sync wrapper for Celery workers."""
    return asyncio.run(scrape_maps_ratings_batch(leads_data, on_progress))


# Legacy single-lead API (kept for backwards compat)
async def scrape_maps_rating(business_name: str, city: str) -> dict:
    results = await scrape_maps_ratings_batch([{"id": "_", "name": business_name, "city": city}])
    return results.get("_", dict(_EMPTY))


def scrape_maps_rating_sync(business_name: str, city: str) -> dict:
    return asyncio.run(scrape_maps_rating(business_name, city))

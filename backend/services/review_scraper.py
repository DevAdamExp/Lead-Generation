"""
services/review_scraper.py — Real review depth from the Google Maps reviews tab.

The pipeline's older "review weaknesses" were arithmetic over rating + count
(review_velocity proxy). This reads actual review *content*: the worst reviews'
text, the true last-review date, the 5★…1★ histogram, and whether the owner
replies to negative reviews. Those are evidence, not inference — the wedge for a
reputation-management pitch.

Design:
  - Pure parsing/summarising helpers (unit-tested, no browser) are separated from
    the Playwright harvesting (best-effort, soft-fail, gated off by default via
    ENABLE_REVIEW_SCRAPE). Selectors mirror google_maps_scraper.py's defensive
    multi-selector style; Google's DOM classes rotate, so the live harvester is
    intentionally forgiving and never raises into the pipeline.
  - Cost control: only run for the EXPORT slice (<= limit leads), never the whole
    candidate pool.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Pure helpers (testable, no browser) ───────────────────────────────────────

_STAR_RE = re.compile(r"([0-5](?:\.\d)?)\s*(?:star|out of)", re.I)


def parse_stars(aria_label: str | None) -> Optional[int]:
    """Extract an integer star rating from an aria-label like
    'Rated 2.0 out of 5' / '1 star'. Returns 1-5 or None."""
    if not aria_label:
        return None
    m = _STAR_RE.search(aria_label)
    if not m:
        return None
    try:
        return max(1, min(5, round(float(m.group(1)))))
    except ValueError:
        return None


def summarize_review_weaknesses(reviews: list[dict],
                                histogram: dict | None,
                                owner_responds: Optional[bool]) -> tuple[str | None, int]:
    """Turn harvested review evidence into concrete weaknesses + a count.
    Replaces the rating-arithmetic proxy with real signals. `reviews` items look
    like {"stars": int, "date": str, "text": str}."""
    weaknesses: list[str] = []
    count = 0

    negatives = [r for r in reviews if isinstance(r.get("stars"), int) and r["stars"] <= 2]
    if negatives:
        weaknesses.append(f"{len(negatives)} recent 1-2★ review(s) with complaint text")
        count += min(len(negatives), 3)

    if histogram:
        low = (histogram.get("1", 0) or 0) + (histogram.get("2", 0) or 0)
        total = sum((histogram.get(str(s), 0) or 0) for s in range(1, 6))
        if total and low / total >= 0.2:
            weaknesses.append(f"{round(100 * low / total)}% of reviews are 1-2★")
            count += 2

    if owner_responds is False and negatives:
        weaknesses.append("Owner does not reply to negative reviews (reputation-management gap)")
        count += 1

    return (", ".join(weaknesses) if weaknesses else None), count


def build_theme_input(reviews: list[dict], max_texts: int = 10) -> list[str]:
    """The <=N worst review texts to hand to the LLM for theme extraction — the
    same NVIDIA pitch-research call, one extra JSON field, zero extra requests."""
    worst = sorted((r for r in reviews if r.get("text")),
                   key=lambda r: (r.get("stars") if isinstance(r.get("stars"), int) else 5))
    return [r["text"] for r in worst[:max_texts]]


# ── Playwright harvesting (best-effort, gated) ────────────────────────────────

_REVIEW_ITEM_SELECTORS = [
    "div[data-review-id]",
    "div.jftiEf",                      # current Maps review card
    "div[class*='review']",
]
_REVIEW_TEXT_SELECTORS = ["span.wiI7pd", "div[class*='review-full-text']", "span[jsname]"]
_REVIEW_STAR_SELECTORS = ["span[role='img'][aria-label*='star']", "[aria-label*='star']"]
_REVIEW_DATE_SELECTORS = ["span.rsqaWe", "span[class*='date']"]
_OWNER_REPLY_SELECTORS = ["div.CDe7pd", "div[class*='owner-response']",
                          "div[class*='response']"]
_SORT_BUTTON_SELECTORS = ["button[aria-label*='Sort']", "button[data-value='Sort']"]


async def open_reviews_pane(page) -> bool:
    """Get from a place panel to its reviews list. True if reviews are showing.

    Maps exposes the reviews list through several entry points depending on the
    layout served, so try them in order of reliability rather than assuming a
    single "Reviews" tab exists:
      1. a role=tab whose label starts "Reviews for ..."
      2. the header rating block — the review COUNT is a button
      3. a "More reviews" / "All reviews" action button

    Returns False when none of them appear, which is the signal that Maps served
    a degraded panel (see _reviews_look_unavailable) rather than that the
    business has no reviews.
    """
    candidates = [
        "button[role='tab'][aria-label^='Reviews']",
        "button[aria-label^='Reviews for']",
        "div[role='tab'][aria-label^='Reviews']",
        "button[jsaction*='moreReviews']",
        "button[aria-label*='More reviews']",
        "button[aria-label*='All reviews']",
        "div.F7nice button",
        "button[data-tab-index='1']",
    ]
    for sel in candidates:
        try:
            btn = page.locator(sel).first
            if await btn.count() == 0:
                continue
            await btn.click(timeout=4000)
            await asyncio.sleep(random.uniform(1.4, 2.2))
            for probe in _REVIEW_ITEM_SELECTORS:
                if await page.locator(probe).count() > 0:
                    return True
        except Exception:
            continue
    # Some layouts render the list without any click at all.
    for probe in _REVIEW_ITEM_SELECTORS:
        try:
            if await page.locator(probe).count() > 0:
                return True
        except Exception:
            continue
    return False


async def reviews_look_unavailable(page) -> bool:
    """Did Maps serve a degraded panel rather than a genuinely review-less place?

    The tell: the header shows a rating but NO review count. A real place page
    renders "4.9\\n(418)"; a throttled one renders just "4.9". Distinguishing
    these matters — otherwise a soft-block looks identical to "this business has
    no reviews" and the pipeline records a false negative.
    """
    try:
        if await page.locator("div.F7nice").count() == 0:
            return True
        txt = await page.locator("div.F7nice").first.inner_text()
        return not re.search(r"\(\s*[\d,]+\s*\)", txt)
    except Exception:
        return True


async def harvest_reviews(page, max_reviews: int = 10) -> dict:
    """From an already-open Maps place page, open Reviews, sort lowest-first, and
    harvest up to `max_reviews` review cards. Best-effort — returns whatever it
    can, never raises. Returns {reviews, histogram, last_review_date,
    owner_responds, unavailable}."""
    out: dict = {"reviews": [], "histogram": None, "last_review_date": None,
                 "owner_responds": None, "unavailable": False}
    try:
        if not await open_reviews_pane(page):
            out["unavailable"] = await reviews_look_unavailable(page)
            if out["unavailable"]:
                logger.warning("Maps served a panel with no review count — the "
                               "reviews pane is unavailable (usually IP throttling), "
                               "not an absence of reviews")
            return out

        # Grab the newest review's date BEFORE sorting. Maps' default order is
        # most-relevant/recent; after the lowest-first sort below, reviews[0] is
        # the WORST review, not the most recent — which is what last_review_date
        # used to (wrongly) record.
        for sel in _REVIEW_DATE_SELECTORS:
            el = page.locator(sel).first
            if await el.count() > 0:
                out["last_review_date"] = (await el.inner_text()).strip()
                break

        # Sort by lowest rating (worst-first surfaces the weakness signal).
        for sel in _SORT_BUTTON_SELECTORS:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click()
                await asyncio.sleep(0.8)
                lowest = page.get_by_text(re.compile(r"lowest", re.I)).first
                if await lowest.count() > 0:
                    await lowest.click()
                    await asyncio.sleep(random.uniform(1.2, 2.0))
                break

        # Harvest review cards.
        items = None
        for sel in _REVIEW_ITEM_SELECTORS:
            loc = page.locator(sel)
            if await loc.count() > 0:
                items = loc
                break
        if items is None:
            return out

        n = min(await items.count(), max_reviews)
        owner_reply_seen = False
        for i in range(n):
            card = items.nth(i)
            review = {"stars": None, "date": None, "text": None}
            for s in _REVIEW_STAR_SELECTORS:
                el = card.locator(s).first
                if await el.count() > 0:
                    review["stars"] = parse_stars(await el.get_attribute("aria-label"))
                    break
            for s in _REVIEW_TEXT_SELECTORS:
                el = card.locator(s).first
                if await el.count() > 0:
                    review["text"] = (await el.inner_text()).strip()
                    break
            for s in _REVIEW_DATE_SELECTORS:
                el = card.locator(s).first
                if await el.count() > 0:
                    review["date"] = (await el.inner_text()).strip()
                    break
            for s in _OWNER_REPLY_SELECTORS:
                if await card.locator(s).count() > 0:
                    owner_reply_seen = True
                    break
            if review["text"] or review["stars"]:
                out["reviews"].append(review)

        if out["reviews"]:
            # last_review_date was captured pre-sort above; fall back to the first
            # harvested card only if that lookup found nothing.
            if not out["last_review_date"]:
                out["last_review_date"] = out["reviews"][0].get("date")
            out["owner_responds"] = owner_reply_seen
    except Exception as e:  # noqa: BLE001 — never break the pipeline on review scrape
        logger.info("Review harvest failed: %s", e)
    return out


async def _scrape_reviews_batch(leads_data: list[dict], on_progress=None) -> dict:
    """leads_data: [{id, google_maps_url}]. Returns {lead_id: harvest_dict}.

    Harvesting hits Google Maps place pages, so it inherits the enrichment
    scraper's design: N browser contexts pull from a shared queue, each on its
    OWN Tor circuit (per-context proxy), so Google throttles per-IP not globally.
    Was sequential + direct (one IP, ~1 lead/5s) — a scaling cliff once the
    export slice grows; now it's paced per-worker and concurrent to circuit
    capacity. asyncio is single-threaded, so the shared `results` needs no lock.
    """
    from playwright.async_api import async_playwright
    from backend.services.google_maps_search import _maybe_accept_consent
    import os

    try:
        from playwright_stealth import Stealth
        _stealth = True
    except ImportError:
        _stealth = False

    headless = os.getenv("SCRAPER_HEADLESS", "true").lower() in ("true", "1", "yes")
    results: dict = {}
    targets = [d for d in leads_data if d.get("google_maps_url")]
    if not targets:
        return results

    # Per-worker isolated Tor circuit (Chromium needs the launch sentinel to
    # honour a per-context proxy) — mirrors google_maps_scraper enrichment.
    _rotator = None
    use_proxy = False
    try:
        from backend.services.proxy_rotator import get_rotator
        _rotator = get_rotator()
        use_proxy = _rotator.get_proxy(for_playwright=True, tag="rev0") is not None
    except Exception:
        _rotator = None

    def _worker_proxy(w: int):
        if not (use_proxy and _rotator):
            return None
        try:
            return _rotator.get_proxy(for_playwright=True, tag=f"rev{w}")
        except Exception:
            return None

    async with async_playwright() as p:
        launch_kwargs = {
            "headless": headless,
            "args": ["--no-sandbox", "--disable-setuid-sandbox",
                     "--disable-dev-shm-usage", "--disable-gpu"],
        }
        if use_proxy:
            launch_kwargs["proxy"] = {"server": "per-context"}
        browser = await p.chromium.launch(**launch_kwargs)

        cap = _rotator.circuit_capacity() if (use_proxy and _rotator) else 1
        n_ctx = min(max(2, cap), max(1, len(targets)))

        queue: asyncio.Queue = asyncio.Queue()
        for item in targets:
            queue.put_nowait(item)
        progress = {"n": 0}
        total = len(targets)

        async def _worker(worker_id: int):
            # This context used to set ONLY locale — no user agent, no viewport,
            # no stealth, no consent handling — while every other Maps scraper in
            # the codebase sets all four. It therefore announced itself as
            # HeadlessChrome on the one flow most likely to be rate-limited.
            ctx_kwargs = dict(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            wp = _worker_proxy(worker_id)
            if wp:
                ctx_kwargs["proxy"] = wp
            context = await browser.new_context(**ctx_kwargs)
            try:
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    page = await context.new_page()
                    if _stealth:
                        try:
                            await Stealth().apply_stealth_async(page)
                        except Exception:
                            pass
                    try:
                        await page.goto(item["google_maps_url"], timeout=25000,
                                        wait_until="domcontentloaded")
                        await asyncio.sleep(random.uniform(1.5, 2.5))
                        await _maybe_accept_consent(page)
                        results[item["id"]] = await harvest_reviews(page)
                    except Exception as e:  # noqa: BLE001
                        logger.info("Review scrape failed for %s: %s", item.get("id"), e)
                    finally:
                        await page.close()
                    progress["n"] += 1
                    if on_progress:
                        try:
                            on_progress(progress["n"], total)
                        except Exception:
                            pass
                    await asyncio.sleep(random.uniform(2.0, 3.5))
            finally:
                await context.close()

        await asyncio.gather(*[_worker(w) for w in range(n_ctx)])
        await browser.close()
    return results


def scrape_reviews_batch_sync(leads_data: list[dict], on_progress=None) -> dict:
    """Sync wrapper for Celery."""
    return asyncio.run(_scrape_reviews_batch(leads_data, on_progress))


if __name__ == "__main__":
    # Self-check the pure logic (the only part worth a test — DOM is live-only).
    assert parse_stars("Rated 2.0 out of 5") == 2
    assert parse_stars("1 star") == 1
    assert parse_stars(None) is None
    revs = [{"stars": 1, "date": "a week ago", "text": "rude staff, dirty"},
            {"stars": 5, "date": "2 years ago", "text": "great"}]
    txt, cnt = summarize_review_weaknesses(revs, {"1": 3, "5": 7}, owner_responds=False)
    assert cnt >= 1 and "1-2★" in txt
    assert build_theme_input(revs) == ["rude staff, dirty", "great"]
    print("review_scraper pure-logic self-check OK")

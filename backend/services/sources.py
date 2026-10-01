"""
services/sources.py — Multi-source lead orchestrator.

Runs every enabled lead source (Google Maps primary + Yelp + YellowPages +
Hotfrog), normalises each into a canonical record, then merges & de-duplicates
across sources into a single set of unique businesses — keeping the richest
value for each field and tracking which sources contributed.

This is what makes the `limit` slider meaningful: instead of ~12 from one thin
directory, we pool many sources and return up to `limit` UNIQUE businesses.

Public API:
    gather_leads(niche, location, country, limit, on_progress) -> list[dict]
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Canonical fields every source maps into. Sources may omit fields (left None).
LEAD_FIELDS = (
    "name", "address", "phone", "website", "category", "description",
    "hours", "price_level", "plus_code", "latitude", "longitude",
    "google_rating", "google_review_count", "google_is_open", "google_maps_url",
    "has_google_maps", "hotfrog_url",
    "social_facebook", "social_instagram", "social_twitter", "social_linkedin",
    "source_url",
)

# Per-field source priority: when two sources disagree on a non-empty value,
# the source earlier in this list wins. Google Maps is most authoritative for
# location/contact; others fill gaps.
# NPI is the official US healthcare registry — most authoritative for a medical
# business's legal name / practice address / phone, so it ranks just below Maps.
_FIELD_PRIORITY = {
    "default": ["google_maps", "npi", "hotfrog", "yellowpages", "yelp"],
    "phone": ["npi", "google_maps", "hotfrog", "yellowpages", "yelp"],
    "address": ["google_maps", "npi", "hotfrog", "yellowpages", "yelp"],
    "google_rating": ["google_maps", "yelp", "yellowpages"],
    "google_review_count": ["google_maps", "yelp", "yellowpages"],
    "description": ["google_maps", "hotfrog", "yelp", "yellowpages"],
}


def _normalize_name(name: str) -> str:
    name = re.sub(r"\b(the|inc|llc|ltd|co|company|corp|plc|group)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


def _digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def _blank(rec: dict) -> dict:
    out = {f: None for f in LEAD_FIELDS}
    out["google_review_count"] = 0
    out["has_google_maps"] = False
    out["sources"] = []
    out["_by_source"] = {}
    out.update({k: v for k, v in rec.items() if k in LEAD_FIELDS})
    return out


def _match(a: dict, b: dict) -> bool:
    """Are two records the same business?"""
    pa, pb = _digits(a.get("phone")), _digits(b.get("phone"))
    if pa and pb and pa[-7:] == pb[-7:]:
        return True
    na, nb = _normalize_name(a.get("name") or ""), _normalize_name(b.get("name") or "")
    if not na or not nb:
        return False
    try:
        from rapidfuzz import fuzz
        name_score = fuzz.token_sort_ratio(na, nb)
    except ImportError:
        name_score = 100 if na == nb else 0
    if name_score < 88:
        return False
    # Same name — require some locality agreement to avoid chain collisions
    aa = (a.get("address") or "").lower()
    ab = (b.get("address") or "").lower()
    if aa and ab:
        try:
            from rapidfuzz import fuzz
            return fuzz.partial_ratio(aa, ab) >= 60
        except ImportError:
            return aa[:15] == ab[:15]
    return True


def _merge_into(target: dict, incoming: dict, source: str) -> None:
    """Merge `incoming` (from `source`) into `target`, richest value wins."""
    if source not in target["sources"]:
        target["sources"].append(source)
    target["_by_source"][source] = incoming

    for field in LEAD_FIELDS:
        new_val = incoming.get(field)
        if new_val in (None, "", 0, False):
            continue
        cur_val = target.get(field)
        if cur_val in (None, "", 0, False):
            target[field] = new_val
            continue
        # Both present — resolve by source priority for this field
        order = _FIELD_PRIORITY.get(field, _FIELD_PRIORITY["default"])
        cur_src = _winning_source(target, field, order)
        if _rank(source, order) < _rank(cur_src, order):
            target[field] = new_val
        # description: keep the longest (richest) text
        if field == "description" and isinstance(new_val, str) and isinstance(cur_val, str):
            if len(new_val) > len(cur_val):
                target[field] = new_val


def _winning_source(target: dict, field: str, order: list[str]) -> str:
    """Which source currently 'owns' target[field]."""
    val = target.get(field)
    for src in order:
        rec = target["_by_source"].get(src)
        if rec and rec.get(field) == val:
            return src
    # fallback: first source that has any value for it
    for src in target["sources"]:
        rec = target["_by_source"].get(src)
        if rec and rec.get(field) not in (None, "", 0, False):
            return src
    return "unknown"


def _rank(source: str, order: list[str]) -> int:
    return order.index(source) if source in order else len(order)


def merge_sources(per_source: dict[str, list[dict]]) -> list[dict]:
    """
    Merge {source_name: [records]} into a deduplicated list of canonical leads.
    Google Maps records are seeded first so they anchor each cluster.
    """
    merged: list[dict] = []
    order = ["google_maps", "npi", "hotfrog", "yellowpages", "yelp"]
    for source in order + [s for s in per_source if s not in order]:
        for rec in per_source.get(source, []):
            if not rec.get("name"):
                continue
            placed = False
            for existing in merged:
                if _match(existing, rec):
                    _merge_into(existing, rec, source)
                    placed = True
                    break
            if not placed:
                fresh = _blank(rec)
                _merge_into(fresh, rec, source)
                merged.append(fresh)
    # finalize: stash sources list as comma string for the model, and record how
    # many independent sources agree on the phone (last-7-digit match) — real
    # cross-source corroboration for the accuracy score. Then drop the helper.
    for m in merged:
        m["sources_str"] = ",".join(m.get("sources", []))
        merged_phone = _digits(m.get("phone"))[-7:]
        if merged_phone:
            m["phone_source_count"] = sum(
                1 for rec in m.get("_by_source", {}).values()
                if _digits(rec.get("phone"))[-7:] == merged_phone
            )
        else:
            m["phone_source_count"] = 0
        m.pop("_by_source", None)
    logger.info("Merged %d source-records into %d unique businesses",
                sum(len(v) for v in per_source.values()), len(merged))
    return merged


def run_source(name: str, fn, *args, on_timeout=None, **kwargs) -> list:
    """Run one lead source under a hard deadline, returning [] if it overruns.

    No source had a ceiling. A live job sat 22+ minutes inside Hotfrog — routing
    through a half-dead Tor circuit, no output, no way to move on — and would
    have burned the whole 2h Celery limit before delivering anything. One slow
    source must never stall a job; take what the other sources found instead.

    The overrunning thread is abandoned, not killed: Python cannot interrupt a
    thread blocked in a socket read. It is a daemon thread, so it dies with the
    process, and its result is simply ignored.

    SOURCE_TIMEOUT_SECONDS is the calibration knob — raise it if a genuinely slow
    but productive source is being cut off.
    """
    from backend.utils.retry import call_with_deadline

    try:
        from backend.config import settings as _s
        budget = float(getattr(_s, "SOURCE_TIMEOUT_SECONDS", 240))
    except Exception:
        budget = 240.0

    try:
        result, finished = call_with_deadline(
            lambda: fn(*args, **kwargs), budget, None)
    except Exception as e:
        logger.warning("%s source failed: %s", name, e)
        return []
    if not finished:
        logger.warning("Source '%s' exceeded %.0fs — abandoned; continuing with "
                       "the other sources", name, budget)
        if on_timeout:
            try:
                on_timeout(name, budget)
            except Exception:
                pass
        return []
    return result or []


def gather_leads(
    niche: str,
    location: str,
    country: str = "us",
    limit: int = 50,
    on_progress: Optional[Callable[[str, str], None]] = None,
    only: Optional[set[str]] = None,
) -> list[dict]:
    """
    Run lead sources and return up to `limit` unique, merged businesses.

    on_progress(stage, message) is called as each source runs.
    `only`: if given, run ONLY the named sources (still subject to ENABLED_SOURCES
    gating for yellowpages/yelp). Used by the tiled scraper to run Google Maps
    per-tile but Hotfrog/NPI just once per job.
    """
    def progress(stage: str, msg: str):
        logger.info("[sources] %s: %s", stage, msg)
        if on_progress:
            try:
                on_progress(stage, msg)
            except Exception:
                pass

    def _want(src: str) -> bool:
        return only is None or src in only

    def _timed_out(name: str, budget: float):
        progress(name, f"{name} timed out after {budget:.0f}s — skipping it")

    # Over-fetch per source so that after cross-source dedup we can still reach
    # `limit` unique businesses.
    per_source_limit = max(limit, 20)
    per_source: dict[str, list[dict]] = {}

    # Which sources are enabled (config-gated). YP & Yelp are off by default —
    # they yield ~0 over Tor but cost the most (browser-per-fetch + timeouts).
    try:
        from backend.config import settings as _settings
        _enabled = {s.strip() for s in (_settings.ENABLED_SOURCES or "").split(",") if s.strip()}
    except Exception:
        _enabled = {"google_maps", "npi", "hotfrog"}

    # 1) Google Maps (primary, richest)
    if _want("google_maps"):
        try:
            progress("Google Maps", f"Searching Google Maps for '{niche}' in '{location}'…")
            from backend.services.google_maps_search import search_maps_businesses_sync
            per_source["google_maps"] = run_source(
                "google_maps", search_maps_businesses_sync,
                niche, location, country, limit=per_source_limit,
                on_timeout=_timed_out)
            progress("Google Maps", f"Found {len(per_source['google_maps'])} on Google Maps")
        except Exception as e:
            logger.warning("Google Maps source failed: %s", e)
            per_source["google_maps"] = []

    # 2) YellowPages (gated — off by default, see ENABLED_SOURCES)
    if "yellowpages" in _enabled and _want("yellowpages"):
        try:
            progress("YellowPages", "Searching YellowPages…")
            from backend.services.yellowpages_scraper import search_yellowpages
            per_source["yellowpages"] = run_source(
                "yellowpages", search_yellowpages, niche, location,
                limit=per_source_limit, on_timeout=_timed_out)
            progress("YellowPages", f"Found {len(per_source['yellowpages'])} on YellowPages")
        except Exception as e:
            logger.warning("YellowPages source failed: %s", e)
            per_source["yellowpages"] = []

    # 3) Yelp (gated — off by default, see ENABLED_SOURCES)
    if "yelp" in _enabled and _want("yelp"):
        try:
            progress("Yelp", "Searching Yelp…")
            from backend.services.yelp_scraper import search_yelp
            per_source["yelp"] = run_source(
                "yelp", search_yelp, niche, location,
                limit=per_source_limit, on_timeout=_timed_out)
            progress("Yelp", f"Found {len(per_source['yelp'])} on Yelp")
        except Exception as e:
            logger.warning("Yelp source failed: %s", e)
            per_source["yelp"] = []

    # 4) Hotfrog (legacy source — still useful coverage)
    if _want("hotfrog"):
        try:
            progress("Hotfrog", "Searching Hotfrog…")
            per_source["hotfrog"] = run_source(
                "hotfrog", _gather_hotfrog, niche, location, country,
                per_source_limit, on_timeout=_timed_out)
            progress("Hotfrog", f"Found {len(per_source['hotfrog'])} on Hotfrog")
        except Exception as e:
            logger.warning("Hotfrog source failed: %s", e)
            per_source["hotfrog"] = []

    # 5) NPI registry (official US healthcare data — only fires for medical niches)
    if _want("npi"):
        try:
            from backend.services.npi_source import search_npi
            progress("NPI", "Searching NPI registry…")
            # NPI is cheap (one cached API sweep) and high-volume — let it fetch a
            # big batch so it carries the candidate pool for medical niches instead
            # of leaning on slow Maps tiles.
            per_source["npi"] = run_source(
                "npi", search_npi, niche, location,
                limit=max(per_source_limit, 150), country=country,
                on_timeout=_timed_out)
            if per_source["npi"]:
                progress("NPI", f"Found {len(per_source['npi'])} in NPI registry")
        except Exception as e:
            logger.warning("NPI source failed: %s", e)
            per_source["npi"] = []

    merged = merge_sources(per_source)

    # Rank: prefer businesses confirmed by multiple sources, then those on Maps.
    merged.sort(key=lambda m: (len(m.get("sources", [])), m.get("has_google_maps", False)),
                reverse=True)

    progress("Merge", f"{len(merged)} unique businesses after cross-source dedup")
    return merged[:limit]


def build_tiles(niche: str, location: str) -> list[tuple[str, str]]:
    """Expand one (niche, location) into several query variants so the pooled,
    de-duplicated candidate set is far larger than any single Maps query (~48).

    General-purpose (no per-city data): combines geographic-directional area
    variants (Maps re-centres on each area, giving real spatial diversity) with
    a couple of phrasing variants. Meaningless variants for small towns simply
    return near-duplicate results that dedup away — harmless.
    """
    niche = niche.strip()
    parts = [p.strip() for p in location.split(",") if p.strip()]
    city = parts[0] if parts else location.strip()
    region = parts[1] if len(parts) > 1 else ""

    def loc(mod: str) -> str:
        base = f"{mod}{city}".strip()
        return f"{base}, {region}" if region else base

    tiles = [
        (niche, location),
        (niche, loc("Downtown ")),
        (niche, loc("North ")),
        (niche, loc("South ")),
        (niche, loc("East ")),
        (niche, loc("West ")),
        (niche, loc("Central ")),
        (f"best {niche}", location),
        (f"{niche} services", location),
        (f"top {niche}", location),
    ]
    # Dedupe (city with no region, or empty mods, can collide) preserving order.
    seen, out = set(), []
    for q, l in tiles:
        key = (q.lower(), l.lower())
        if key not in seen:
            seen.add(key)
            out.append((q, l))
    return out


def _merge_pool(pool: list[dict], recs: list[dict]) -> list[dict]:
    """Merge already-canonical records (from another tile) into `pool`, deduping
    across tiles via _match and unioning provenance / filling blank fields."""
    for rec in recs:
        if not rec.get("name"):
            continue
        hit = next((p for p in pool if _match(p, rec)), None)
        if hit is None:
            pool.append(dict(rec))
            continue
        srcs = set(hit.get("sources") or [])
        srcs.update(rec.get("sources") or [])
        hit["sources"] = sorted(srcs)
        hit["sources_str"] = ",".join(hit["sources"])
        hit["phone_source_count"] = max(hit.get("phone_source_count", 0),
                                        rec.get("phone_source_count", 0))
        for f in LEAD_FIELDS:
            nv = rec.get(f)
            if nv in (None, "", 0, False):
                continue
            cur = hit.get(f)
            if cur in (None, "", 0, False):
                hit[f] = nv
            elif f == "description" and isinstance(nv, str) and isinstance(cur, str) and len(nv) > len(cur):
                hit[f] = nv
    return pool


def location_matches(address: Optional[str], location: str) -> bool:
    """Is `address` plausibly inside the requested `location`?

    Guards a real failure: build_tiles turns "Bend, OR" into "South Bend, OR" and
    "West Bend, OR", and Google Maps geocodes those to South Bend INDIANA and West
    Bend WISCONSIN — ignoring the state suffix entirely. A live job for Bend, OR
    returned 7 of 7 businesses in Indiana, and the same tiles put Madison, WI
    addresses into Newark and Houston jobs. Nothing downstream ever checked, so
    those shipped as delivered leads.

    Conservative by design:
      - no state in the request, or no address on the record -> keep it (we
        cannot judge, and NPI/Hotfrog records often have no address)
      - state named in either form (CA / California) counts as a match
    """
    if not location:
        return True
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if len(parts) < 2:
        return True                      # no state given — nothing to check against
    raw = parts[1].strip()
    from backend.services.npi_source import _STATE_ABBR
    abbr = raw.upper() if len(raw) == 2 else _STATE_ABBR.get(raw.lower(), "")
    if not abbr:
        return True                      # unrecognised region — don't guess
    full = next((k for k, v in _STATE_ABBR.items() if v == abbr), "")
    if not address:
        return True                      # unjudgeable — keep, dedup handles dupes
    a = address.lower()
    if re.search(rf"\b{abbr.lower()}\b", a):
        return True
    return bool(full and full in a)


def _verify_likelihood(m: dict) -> tuple:
    """Sort key (desc) for enriching the candidates most likely to verify first.

    Order of signal strength: multi-source corroboration > Google Maps presence >
    NPI (official medical registry = real business w/ valid phone) > has-a-phone >
    has-a-website. This pushes nameless directory noise (Hotfrog deep pages with
    no phone/maps) to the back so it's only enriched if we're still short.
    """
    srcs = m.get("sources", [])
    return (
        len(srcs),
        bool(m.get("has_google_maps")),
        "npi" in srcs,
        bool(m.get("phone")),
        bool(m.get("website")),
    )


def gather_leads_tiled(
    niche: str,
    location: str,
    country: str = "us",
    candidate_target: int = 300,
    per_tile: int = 0,
    on_progress: Optional[Callable[[str, str], None]] = None,
    exclude: Optional[Callable[[dict], bool]] = None,
) -> list[dict]:
    """Run query-variant tiles and merge into a candidate pool aimed at
    `candidate_target` unique businesses (best-effort), ranked best-first.

    Stops early once the pool reaches the target so we don't scrape more tiles
    than needed. `per_tile` auto-sizes to the target (capped at 60) so small
    jobs don't pay for deep Maps scrapes they won't use.

    `exclude(rec) -> bool`: optional predicate; records for which it returns True
    are dropped before merging (used for cross-run dedup — businesses already
    delivered in this niche). Tiling keeps going until `candidate_target` NEW
    businesses are pooled or all tiles are spent.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if per_tile <= 0:
        # Cap Maps depth per tile at 60 (a balance of coverage vs. scrape time;
        # deeper scrolls add minutes per tile for diminishing unique results, and
        # NPI carries volume for medical niches).
        per_tile = max(1, min(60, candidate_target))
    try:
        from backend.config import settings as _settings
        tile_workers = max(1, int(getattr(_settings, "TILE_CONCURRENCY", 4)))
    except Exception:
        tile_workers = 4

    tiles = build_tiles(niche, location)
    pool: list[dict] = []
    excluded_n = 0

    def _apply_exclude(recs: list[dict]) -> list[dict]:
        nonlocal excluded_n
        if not exclude:
            return recs
        kept = [r for r in recs if not exclude(r)]
        excluded_n += len(recs) - len(kept)
        return kept

    # Non-tiled sources run ONCE for the whole job. Hotfrog paginates deep and
    # NPI sweeps 150+ in a single call — re-running them per tile just re-crawls
    # near-identical queries (the old cost: up to 10× Hotfrog crawls per job).
    # Tiling only helps Google Maps, which re-centres on each area/phrasing.
    try:
        once = gather_leads(niche, location, country, limit=candidate_target,
                            only={"hotfrog", "npi", "yellowpages", "yelp"})
        _merge_pool(pool, _apply_exclude(once))
        logger.info("Once-sources (Hotfrog/NPI/…) → pool %d", len(pool))
    except Exception as e:
        logger.warning("Once-source gather failed: %s", e)

    # Google Maps tiles — run concurrently. as_completed yields on THIS thread so
    # _merge_pool (which mutates `pool`) is never touched by two threads at once.
    def _maps_tile(q: str, loc: str) -> list[dict]:
        try:
            return gather_leads(q, loc, country, limit=per_tile, only={"google_maps"})
        except Exception as e:
            logger.warning("Tile '%s' @ '%s' failed: %s", q, loc, e)
            return []

    done = 0
    with ThreadPoolExecutor(max_workers=tile_workers) as ex:
        futures = {ex.submit(_maps_tile, q, loc): (q, loc) for q, loc in tiles}
        for fut in as_completed(futures):
            q, loc = futures[fut]
            done += 1
            _merge_pool(pool, _apply_exclude(fut.result()))
            if on_progress:
                try:
                    on_progress("Tiling",
                                f"tile {done}/{len(tiles)} '{q}' @ '{loc}' → pool {len(pool)}")
                except Exception:
                    pass
            logger.info("Tile %d/%d '%s' @ '%s' → pool now %d",
                        done, len(tiles), q, loc, len(pool))
            # Early exit: stop consuming results once the pool is full. In-flight
            # tiles finish in the background and are simply ignored (~tile_workers
            # wasted at most) — far cheaper than the old sequential re-crawls.
            if len(pool) >= candidate_target:
                break

    if excluded_n:
        logger.info("Skipped %d candidates already delivered in this niche", excluded_n)

    # Drop anything the tiles dragged in from another state (see location_matches).
    before = len(pool)
    pool = [m for m in pool if location_matches(m.get("address"), location)]
    if before != len(pool):
        logger.warning("Location filter dropped %d/%d candidates outside '%s'",
                       before - len(pool), before, location)
        if on_progress:
            try:
                on_progress("Tiling",
                            f"dropped {before - len(pool)} results from outside {location}")
            except Exception:
                pass

    pool.sort(key=_verify_likelihood, reverse=True)
    return pool[:candidate_target]


def _gather_hotfrog(niche: str, location: str, country: str, limit: int) -> list[dict]:
    """Adapt the existing Hotfrog scraper into canonical records."""
    import sys
    from pathlib import Path
    hf_path = str(Path(__file__).parent.parent.parent / "hotfrog")
    if hf_path not in sys.path:
        sys.path.insert(0, hf_path)
    import hotfrog_core as core

    query = f"{niche} {location}".strip()
    out: list[dict] = []
    seen_names: set[str] = set()
    page = 1
    # Hotfrog shows ~12 listings/page behind a Cloudflare challenge. Posture (per
    # the goal-3 audit): ONE warm session > many rotating IPs — a fresh IP per
    # page re-triggers the JS challenge every time and reads as bot traffic. So we
    # keep the same session and only rotate the IP AFTER a block (403/empty),
    # which is when a new identity actually helps. The circuit breaker in
    # hotfrog_core still hard-stops a wall.
    #
    # ponytail: the full "all leads in one search" win (sitemap enumeration with a
    # harvested cf_clearance cookie, or Common Crawl/Wayback offline) needs live
    # Cloudflare probing to build safely — deliberately left as documented next
    # work rather than shipped blind. See docs/audits/status-report.md §3.
    per_page = 12
    max_pages = 50
    try:
        from backend.config import settings as _settings
        delay = float(getattr(_settings, "HOTFROG_REQUEST_DELAY", 1.5))
    except Exception:
        delay = 1.5
    consecutive_fail = 0
    while len(out) < limit and page <= max_pages:
        # Warm session by default; only take a new IP once the last attempt(s)
        # failed — a block is the one time a fresh identity is worth the re-challenge.
        rotate = consecutive_fail > 0
        try:
            batch = core.search_businesses(query, country, limit=per_page,
                                           page=page, rotate_proxy=rotate)
        except Exception as e:
            logger.info("Hotfrog page %d failed: %s", page, e)
            consecutive_fail += 1
            if consecutive_fail >= 3:
                break
            page += 1
            time.sleep(delay)
            continue
        if not batch:
            consecutive_fail += 1
            if consecutive_fail >= 2:
                break
            page += 1
            time.sleep(delay)
            continue
        consecutive_fail = 0
        new = 0
        for r in batch:
            name = (r.get("name") or "").strip()
            key = name.lower()
            if not key or key in seen_names:
                continue  # Hotfrog re-serves the last page past the end — skip dups
            seen_names.add(key)
            out.append({
                "name": name,
                "address": r.get("address"),
                "phone": r.get("phone"),
                "website": r.get("website"),
                "description": r.get("description"),
                "hotfrog_url": r.get("hotfrog_url"),
                "social_facebook": r.get("social_facebook"),
                "social_instagram": r.get("social_instagram"),
                "social_twitter": r.get("social_twitter"),
                "social_linkedin": r.get("social_linkedin"),
                "source": "hotfrog",
            })
            new += 1
        # Stop once a page yields no new businesses (pagination exhausted).
        if new == 0:
            break
        # Respect a known total_pages when the parser found one.
        pag = getattr(batch, "_pagination", None)
        if pag and pag.get("total_pages") and page >= pag["total_pages"]:
            break
        page += 1
        time.sleep(delay)  # pace requests so Hotfrog doesn't 403 the deep crawl
    return out[:limit]

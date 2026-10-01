---
title: Scraper Configuration
type: reference
tags: [scraper, reference, config]
source: backend/config.py
updated: 2026-07-07
---

# Scraper Configuration — the knobs

> [!info] All in `backend/config.py` (`Settings`)
> Parent: [[Scraper System]] · Proxy knobs live separately in [[Configuration]] (tor-proxy vault)

## Which sources run

| Setting | Default | Meaning |
|---------|---------|---------|
| `ENABLED_SOURCES` | `google_maps,npi,hotfrog` | comma list; YP/Yelp off (~0 yield over Tor) → [[Directory Scrapers]] |
| `ENABLE_REVIEW_SCRAPE` | `False` | deep [[Review Scraper]] on the export slice |
| `ENABLE_REGISTRATION_LOOKUP` | `False` | legal-entity lookup on the export slice |
| `SCRAPER_HEADLESS` | `True` | headless Chromium (no window steals focus) |

## Volume & pacing

| Setting | Default | Meaning |
|---------|---------|---------|
| `TILE_CONCURRENCY` | `4` | parallel Maps query-variant tiles → [[Sources Orchestrator]] |
| `MAPS_ENRICH_CONCURRENCY` | `4` | **desired** enrichment contexts — capped by [[Circuit Capacity and Concurrency|`circuit_capacity()`]] |
| `MAPS_ENRICH_DELAY_MIN` / `MAX` | `1.5` / `3.0` | per-worker polite delay (the live-tuning knob) |
| `ENRICH_BATCH_SIZE` | `50` | leads per batch before re-checking the target |
| `CONTACT_CONCURRENCY` | `10` | parallel per-lead website/contact crawl |
| `HOTFROG_REQUEST_DELAY` | `1.5` | seconds between Hotfrog pages → [[Hotfrog]] |

## Pool sizing

| Setting | Default | Meaning |
|---------|---------|---------|
| `POOL_FACTOR` | `1.5` | **main runtime lever**: `pool = limit × this` |
| `POOL_MAX` | `800` | hard cap on candidates scraped |

> [!tip] `POOL_FACTOR` is the speed/coverage dial
> Bigger pool → more candidates to enrich → higher chance of hitting the verified target, but more scrape time. See [[Pipeline Integration#Pool sizing]].

## Accuracy gates

| Setting | Default | Gate |
|---------|---------|------|
| `VERIFY_MIN_SCORE` | `40` | score at/above which a lead is "verified" |
| `MIN_EXPORT_CONFIDENCE` | `0.6` | floor for the strict export gate |

Both feed [[Accuracy and Verification]].

## Per-scraper constants (not in config)

- `_PROXY_RETRIES = 2` — fresh-circuit retries in [[Directory Scrapers]].
- `TIMEOUT = 4` — directory fail-fast (yelp/yp).
- `_CHALLENGE_TITLES` / 16s ceiling — [[Hotfrog]] challenge wait.

#scraper/reference

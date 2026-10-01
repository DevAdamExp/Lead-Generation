---
title: Configuration
type: reference
tags: [tor-proxy, reference, config]
source: backend/config.py
updated: 2026-07-05
---

# Configuration — the knobs

> [!info] All in `backend/config.py` (`Settings`)
> Parent: [[Tor Proxy System]] · Read into the pool by `_build_config_from_settings()` in [[ProxyRotator]]

## Proxy pool

| Setting | Default | Meaning |
|---------|---------|---------|
| `TOR_SOCKS_PORTS` | `"9050,9052,9053,9054,9055"` | SOCKS ports to probe — **one daemon, many ports** ([[Tor Daemon Deployment]]) |
| `TOR_CONTROL_PORTS` | `"9051"` | control port(s) for `NEWNYM` fallback |
| `EXTERNAL_SOCKS_PROXIES` | `""` | optional real SOCKS proxies → residential tier ([[Proxy Strategy Tiers]]) |
| `IP_ROTATION_COOLDOWN` | `30` | min seconds between rotations (cooldown gate) |
| `IP_ROTATE_EVERY_N_REQUESTS` | `0` | auto-rotate every N hand-outs (0 = off) |

## Concurrency & pacing

| Setting | Default | Meaning |
|---------|---------|---------|
| `TILE_CONCURRENCY` | `4` | parallel Maps query-variant tiles |
| `MAPS_ENRICH_CONCURRENCY` | `4` | **desired** enrichment contexts — capped by `circuit_capacity()` at runtime → [[Circuit Capacity and Concurrency]] |
| `MAPS_ENRICH_DELAY_MIN` | `1.5` | min polite delay between a worker's Maps queries |
| `MAPS_ENRICH_DELAY_MAX` | `3.0` | max polite delay |
| `ENRICH_BATCH_SIZE` | `50` | leads enriched per batch before re-checking target |

> [!tip] The calibration knob
> `MAPS_ENRICH_DELAY_MIN/MAX` is the one pair to tune against **live** Google behavior. Lower = faster but riskier. Because each context now has its own IP, per-IP rate is already far below the old regime (see the math in [[Circuit Capacity and Concurrency]]), so the defaults are conservative — but only a live run confirms the floor.

## Module-level defaults (fallback if config import fails)

In `proxy_rotator.py`:
```python
DEFAULT_TOR_PORTS         = [9050, 9052, 9053, 9054, 9055]
DEFAULT_TOR_CONTROL_PORTS = [9051]
CIRCUIT_WAIT_SECONDS      = 5     # NEWNYM settle
IP_CACHE_TTL              = 60    # exit-IP cache
REDISCOVER_COOLDOWN       = 30    # empty-pool re-probe
```

> [!note] Keep the two in sync
> The module defaults **mirror** `config.py` on purpose, so a failed settings import narrows to the *same* pool rather than a lone port.

## Per-scraper constant

`_PROXY_RETRIES = 2` in `yelp_scraper.py` / `yellowpages_scraper.py` — fresh-circuit retries before the Playwright fallback ([[Failure Classification and Retry]]).

#tor-proxy/reference

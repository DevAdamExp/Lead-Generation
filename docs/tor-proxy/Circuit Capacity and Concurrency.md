---
title: Circuit Capacity and Concurrency
type: concept
tags: [tor-proxy, concurrency, performance, phase-3]
source: backend/services/proxy_rotator.py, backend/services/google_maps_scraper.py
updated: 2026-07-05
---

# Circuit Capacity and Concurrency

> [!info] Phase 3 — the wall-clock win
> Parent: [[Tor Proxy System]] · Uses: [[get_proxy and Tags]] · Enabled by: [[Circuit Isolation]]

## `circuit_capacity()` — how many distinct IPs you *actually* have

`ProxyRotator.circuit_capacity()` (line 444):

```python
external proxies present → len(external)
else healthy tor ports    → that count
else                      → 1   # direct: a single shared IP
```

> [!important] Why this exists
> Concurrency is only safe **up to the number of distinct exit IPs**. Stack more workers than IPs and several share one circuit → per-IP request rate climbs → blocks. So workers must size themselves to *real* capacity, not a guessed constant.

## Sizing enrichment workers (`google_maps_scraper.py`)

```python
# line ~500
cap  = _rotator.circuit_capacity() if (use_proxy and _rotator) else 1
n_ctx = min(want, max(2, cap), max(1, len(leads_data)))
```

| Situation | `cap` | `n_ctx` (want=4) | Meaning |
|-----------|:--:|:--:|---------|
| 5 SocksPorts (migrated) | 5 | **4** | 4 contexts, 4 IPs |
| 1 port (un-migrated) | 1 | **2** | old default, no regression |
| Direct (no Tor) | 1 | **2** | old default, one shared IP |
| 1 lead only | any | 1 | nothing to parallelize |

> [!note] The `max(2, cap)` floor
> Historically 2 contexts shared 1 IP — an accepted tradeoff. The floor preserves that everywhere, then scales **up** to one context per IP when ports exist. So migration only ever *adds* speed.

## The per-IP-rate math (why raising concurrency is *safe*)

The counter-intuitive result: with per-context IPs you can run **more** contexts at a **shorter** delay and still hit each IP **less** often.

| | Contexts | IPs | Delay | Requests **per IP** |
|--|:--:|:--:|:--:|:--:|
| **Before** | 2 | 1 (shared) | 2.0–3.5 s | ~1 / **1.4 s** |
| **After** | 4 | 4 | 1.5–3.0 s | ~1 / **2.25 s** |

Per-IP rate **drops** (1.4 s → 2.25 s) while wall-clock roughly **halves**. The concurrency dial is strictly safer per-IP than before; the delay is the calibration knob.

> [!tip] The one number that still needs a live run
> `MAPS_ENRICH_DELAY_MIN` (default 1.5 s) can't be proven safe by unit tests — Google's tolerance is empirical. Dial it down on a live job until you see the first consent wall, then back off. This is the deliberate "calibration knob." See [[Configuration]] and [[Roadmap and YAGNI#Deliberately not built]].

## Where concurrency lives

- **Tiling** (`sources.py`): `TILE_CONCURRENCY=4` threads, each a Maps query — round-robin proxies (no tag needed; threads naturally spread across ports).
- **Enrichment** (`google_maps_scraper.py`): `n_ctx` asyncio browser contexts, each a **tagged** circuit → [[Consumers]].
- **Thread-safety** of the shared singleton under all this: [[Health and Thread-Safety]].

## Not built (on purpose)
Pre-warming circuits and HTTP/2 were considered and skipped — see [[Roadmap and YAGNI#Deliberately not built]].

#tor-proxy/concept #tor-proxy/phase-3

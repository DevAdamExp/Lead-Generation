---
title: Roadmap and YAGNI
type: reference
tags: [tor-proxy, roadmap, decisions]
updated: 2026-07-05
---

# Roadmap and YAGNI

> [!info] What shipped, what's next, what was deliberately *not* built
> Parent: [[Tor Proxy System]]

## The four phases

| Phase | Concept | State | Note |
|:--:|---------|:--:|------|
| **1** | [[Circuit Isolation]] — per-worker exit IPs | ✅ | one daemon; SOCKS-auth (httpx) + SocksPort (Chromium); graceful single-port degrade |
| **2** | [[Failure Classification and Retry]] | ✅ | classify → retry on fresh circuit; bail fast on target-down |
| **3** | [[Circuit Capacity and Concurrency]] | ✅ | concurrency scaled to real IP diversity + tunable delay → ~2× faster |
| **4** | [[Proxy Strategy Tiers]] — residential tier | 🔲 | optional, $$; already plumbed via `EXTERNAL_SOCKS_PROXIES` |

## Validation status

- ✅ **439 unit tests** green (isolation shapes, capacity, retry buckets, thread-safety contracts, legacy no-tag contract).
- 🔲 **Live**: distinct exit IPs across ports + real block-rate + `MAPS_ENRICH_DELAY_MIN` floor. Needs a real Tor run → [[Tor Daemon Deployment#Live validation]].

## Deliberately not built

> [!quote] YAGNI ledger — why each was skipped
> Documenting the *no*s so they don't get silently re-added.

| Not built | Why | Revisit when |
|-----------|-----|--------------|
| **Per-tag EWMA circuit scoring** | tags are single-use (no history); 5 ports share one daemon's guards → ~no per-port signal; real variance is per-exit-circuit and ephemeral | job volume 10×'s and repeated exits become measurable |
| **Pre-warming circuits** | the N workers already build circuits **in parallel** on first request — no serialization to hide | requests become serial |
| **HTTP/2** | `h2` not installed; single-page scraping barely benefits; reused clients already keep-alive | many requests to the same host dominate |
| **Rewiring `contact_finder` / `business_intel`** | they hit business sites (not blockers) and already have `retry_sync`; real regression risk for marginal gain | those sites start IP-blocking |
| **Distributed proxy mesh / Redis circuit registry / async health daemon / ML block-prediction** | pure cost at ~22 leads/job | scale hits 100k+ req/day |

## Next actions (if continuing)

1. Deploy multi-port `torrc`, run `get_all_ips()` — confirm distinct IPs ([[Tor Daemon Deployment]]).
2. One live enrichment job; dial `MAPS_ENRICH_DELAY_MIN` to the block edge ([[Configuration]]).
3. (Optional) Phase 4: buy a residential pool, fill `EXTERNAL_SOCKS_PROXIES`, route Yelp/YP through it ([[Proxy Strategy Tiers]]).

#tor-proxy/reference #tor-proxy/decisions

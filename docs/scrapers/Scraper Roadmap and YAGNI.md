---
title: Scraper Roadmap and YAGNI
type: reference
tags: [scraper, roadmap, decisions]
updated: 2026-07-07
---

# Scraper Roadmap and YAGNI

> [!info] What was fixed, what's next, what was deliberately NOT built
> Parent: [[Scraper System]]

## Fixed (2026-07-07 hardening pass)

| # | Sev | Finding | Fix |
|:-:|:--:|---------|-----|
| **1-3** | High | [[Google Maps Enrichment]] read page-wide (wrong feed card's rating), proceeded on name-mismatch, used brittle CSS | Delegate to [[Maps Extraction Internals|`_extract_detail`]]: panel-scoped + `data-item-id`, navigate-to-matched-place, `_EMPTY` on mismatch. New pure `_pick_best_place` unit-tested. |
| **4** | Med | [[Hotfrog]] challenge-solver ground 30s clicking un-solvable Turnstile; `_parse_pagination` computed 7 fields, 1 used | Wait-only challenge (16s ceiling); pagination **181→21 lines** (`total_pages` only) |
| **5** | Low | [[Review Scraper]] sequential + direct (one IP) | Per-context Tor circuits + concurrent queue |
| **6** | Low | Dead `_DEDUP_CACHE` state, duplicate hotfrog selector | Deleted |

Net: **−331 lines**, 437 unit tests green.

## Validation status

- ✅ **Unit**: `_pick_best_place` ranking, hotfrog pagination, review pure-logic, breaker behavior.
- 🔲 **Live-only**: does `_extract_detail` read the right panel on **today's** Maps markup; does the 16s challenge ceiling hold. Needs one real browser+Tor run → [[Tor Daemon Deployment#Live validation]].

## Deliberately not built

> [!quote] YAGNI ledger — why each was skipped
| Not built | Why | Revisit when |
|-----------|-----|--------------|
| **Merge the two Maps modules into one** | they answer different questions ([[Discovery vs Enrichment]]); they already share the *reader* — that was the real duplication | a third Maps caller appears |
| **Shared "N proxied contexts over a queue" helper** | 2 call sites ([[Google Maps Enrichment]], [[Review Scraper]]) differ in per-item work + delay; a HOF would abstract little | a 3rd concurrent-browser scraper lands |
| **Rescue Yelp/YP over Tor** | directories blocklist Tor exits — no tuning beats that; they're plumbed for [[Proxy Strategy Tiers|residential proxies]] instead | `EXTERNAL_SOCKS_PROXIES` is funded |
| **Solve Cloudflare Turnstile** | not reliably click-solvable; grinding it is pure cost | a CAPTCHA-solving budget exists |
| **Hotfrog sitemap / Common-Crawl enumeration** | needs live CF probing to build safely | Hotfrog becomes a priority source |

## Next actions (if continuing)

1. One live enrichment run on a real niche → confirm the [[Maps Extraction Internals|panel-scoped]] reader end-to-end.
2. Dial `MAPS_ENRICH_DELAY_MIN` to the block edge on live Google behaviour ([[Scraper Configuration]]).
3. (Optional) fund a residential pool → flip on [[Directory Scrapers]] via [[Proxy Strategy Tiers]].

#scraper/reference #scraper/decisions

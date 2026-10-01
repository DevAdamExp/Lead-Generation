# Session Context / Handoff

_Last updated: 2026-06-29. Supersedes the 2026-06-25 handoff._

---

## 1. What this project is

Automated B2B lead generation. Pipeline:
`scrape sources → merge/dedupe → Maps enrich → website check → business intel →
contact enrich → score/verify → (optional LLM pitch research) → export XLSX+PDF`.

- Backend: FastAPI + Celery, SQLite (`leads.db`), Redis broker.
- Frontend: Next.js (`frontend/`).
- Sources: Google Maps (primary), NPI/NPPES (clean, keyless), Hotfrog,
  YellowPages, Yelp — merged & deduped in `backend/services/sources.py`.
- Orchestration: `backend/workers/pipeline.py::run_pipeline`.

Health: **26 backend tests pass (core), 147 frontend tests pass.**

---

## 2. This session (2026-06-25) — deep analysis + refinement

A 5-agent deep audit ran first; then fixes were implemented across the stack.

### Performance (the ~2h-for-200-leads problem)
Root cause: time was dominated by **waiting on doomed requests** — Tor latency +
full-timeout-then-403 on the 3 blocklisted sources, over a hugely over-provisioned
candidate pool. Fixes:
- `config.py` `POOL_FACTOR` 2.6 → **1.5** (enrich ~1.5× target, not 2.6×).
- `config.py` `ENABLED_SOURCES="google_maps,npi,hotfrog"` — **YellowPages & Yelp OFF
  by default** (they yield ~0 over Tor but cost the most time). Re-enable via env
  once residential proxies exist. Gated in `sources.py::gather_leads`.
- `yellowpages_scraper.py` / `yelp_scraper.py` `TIMEOUT` 8 → **4s**.
- `celery_app.py` `task_soft_time_limit=3600`, `task_time_limit=3900`.

### Data-corruption bugs fixed (were silently wrecking every export)
- **Maps-data wipe**: `_stage_maps` applied enrichment across the whole batch,
  blanking `google_rating`/`reviews`/`has_google_maps` on Maps-sourced leads. Now
  applies only to the leads actually enriched.
- **Empty "No Website (Hot)" sheet/section**: `category` was never set to
  `NO_WEBSITE`. Now `_stage_score` sets `NO_WEBSITE` when no active website,
  `HAS_WEBSITE` otherwise → the headline hot-leads sheet/section populates.

### Scraper yield + bug fixes (`backend/services/`)
- **Google Maps under-scroll** (`google_maps_search.py::_scroll_feed`): counted the
  wrong selector and bailed after 8 blind-sleep rounds (~22 results). Now counts
  `a.hfpxzc` (the harvest selector) and waits for the count to actually grow before
  counting a round as stagnant → scrolls toward `limit`.
- **`lstrip("www.")` domain corruption** (stripped a char-set; `www.web.com`→`eb.com`)
  fixed → `removeprefix("www.")` in `contact_finder.py`, `business_intel.py`,
  `owner_contact.py`.
- **httpx `proxies=` removed in ≥0.28** → `proxy=` in `proxy_rotator.py`
  (post-NEWNYM IP verification works again).
- **Email yield**: `contact_finder.py` now canonicalizes the website URL (unwraps
  Google `/url?q=` redirects, strips `utm_*`) before crawling, and no longer skips
  the crawl on a false-`DEAD` verdict (JS/SPA sites). Self-check in `__main__`.

### Local-LLM pitch research (NEW, off by default)
- `backend/services/pitch_research.py` — per-lead pitch via **Ollama** (free, local).
  Synthesis over already-scraped fields (rating, CMS, SSL, owner/title, size, age,
  socials, location, reviews, weaknesses) → `pitch_angle` (the exact service/system
  to sell + why), `pain_points`, `opener`. Soft-fails if Ollama is down.
- Model columns `pitch_angle`/`pain_points`/`opener` added to `models.py`; shown in
  XLSX + PDF.
- Wired in `pipeline.py` AFTER scoring, BEFORE export, **only on the delivered slice**
  (tiered — never the whole funnel), guarded by `ENABLE_PITCH_RESEARCH` (default False).
- **Setup:** `brew install ollama && ollama serve && ollama pull qwen2.5:7b-instruct-q4_K_M`
  then `.env`: `ENABLE_PITCH_RESEARCH=true`. RAM-tight (M1 Air 16GB, browser open):
  use `qwen2.5:3b-instruct-q4_K_M` and set `OLLAMA_MODEL`. Run with browser closed.

### Tor multi-IP (free path chosen by user)
- `scripts/start_tor_instances.sh` already launches N instances; tuned
  `NewCircuitPeriod`/`MaxCircuitDirtiness` 600 → **120s** for fresher exit IP per batch.
- `.env.example` documents **5 instances** (9050…9450). Run: `./scripts/start_tor_instances.sh 5`.
- Truth: more Tor IPs help Google Maps rate-limits; they do **not** unblock
  Cloudflare/PerimeterX (YP/Yelp/Hotfrog) — that needs residential proxies.

### Deliverable polish
- **PDF redesigned** (`pdf_builder.py`): both gradients removed; restrained corporate
  palette (slate `#1f2937` + neutral grays, zebra rows, solid muted source chips),
  header/KPI band, `page-break-inside: avoid`. Self-check asserts no gradients.
- **File management** (`pipeline.py`, `xlsx_builder.py`, `naming.py`): on-disk files
  now use the readable base (`{niche}_{location}_leads.xlsx/.pdf`) inside the readable
  `export_dirname` folder; a **`manifest.json`** per export (job meta, counts,
  score min/max/avg, file list).
- **Export quality**: delivered set sorted by `lead_score` desc then `data_confidence`,
  and filtered to **contactable leads** (drop no-phone AND no-email AND no-website),
  with an empty-set fallback so a deliverable is never empty.

---

## 2b. Pitch intelligence, vertical research & new signals (2026-06-25, cont.)

Goal: make per-lead outreach niche-aware and credible. Focus verticals:
**construction (primary), restaurants, hospitality.**

- **Duplicate-lead fix**: `_stage_scrape` now wipes a job's existing leads before insert
  and commits the whole scrape as ONE transaction → a Celery retry/crash rebuilds instead
  of double-inserting. Test: `tests/test_scrape_idempotent.py`.
- **Vertical knowledge base** — `research/` (`README.md`, `construction_us.md`,
  `construction_subtrades_us.md`, `restaurants_us.md`, `hospitality_us.md`): how each US
  niche works — money flow, licensing, taxes, **mandatory recordkeeping/retention**,
  labor/safety, process flow, pain points → what an agency sells. State-variable items
  flagged `[STATE]` (verify before relying).
- **`backend/services/vertical_profiles.py`** — encodes the research as
  `category → Profile{revenue_model, pains, pitch_angles, signals, opener_hint}`.
  `match_profile(business_category)` keyword-maps a lead to its profile (13 profiles + a
  generic fallback; construction sub-trades distinguished, e.g. HVAC=recurring,
  roofing=insurance/one-shot). Wired into the pitch LLM prompt → a roofer is pitched like
  a roofer. Test: `tests/test_vertical_profiles.py` / self-check in the module.
- **New free signals feeding the pitch:**
  - **Commission-bleed wedge** — `business_intel.detect_platforms()` scans the business's
    own homepage for links to high-commission 3rd parties (DoorDash/UberEats/Grubhub;
    Booking/Expedia/Airbnb) vs a first-party order/booking system. Stored as
    `external_platforms` + `has_direct_commerce` (model cols); shown in XLSX ("Relies On
    (3rd-party)") and the pitch prompt. Neutral for non-commerce trades. Test:
    `tests/test_platform_detect.py`. (Homepage-only scan = ~90%; deeper-page join is the
    upgrade.)
  - **Review velocity** — `business_intel.review_velocity(count, year_founded)` = reviews/
    year from existing fields (no new I/O, type-safe). Adds a "low review velocity"
    weakness (scoring + export) and a `~X/yr` figure in the pitch prompt. Powers the
    review-generation wedge. Test: `tests/test_review_velocity.py`. NOTE: velocity, not true
    last-review *recency* (which would need scraping the reviews tab — deferred).
  - **Marketing stack / paid-ads** — `business_intel.detect_marketing_stack()` scans the
    homepage for ad/marketing pixels (FB Pixel, Google Ads/Tag Manager, GA, TikTok/LinkedIn/
    Twitter). Stored as `marketing_stack` + `runs_paid_ads` (a retargeting/conversion pixel
    ⇒ paid spend). Replaces the fragile Meta Ad Library API (which doesn't cover commercial
    ads). Shown in XLSX ("Marketing Stack") + pitch prompt; tells you which channel to
    critique. Test: `tests/test_marketing_stack.py`.
- **Pitch prompt enriched** (`pitch_research.py`): now feeds rating/CMS/SSL/owner/title/
  size/age/socials/location/price + the two new signals + vertical context.

### Signal roadmap (free, from the research)
✅ commission-bleed (delivery/OTA) · ✅ review velocity · ✅ marketing-stack/paid-ads
(on-page pixel detection — replaces the fragile Ad Library API; `detect_marketing_stack`)
· ⬜ state contractor-license lookup (construction; 50 boards, high-effort) · ⬜ true
review *recency* (needs reviews-tab scraping).

---

## 3. Key config (`backend/config.py` / `.env`)

| Var | Default | Purpose |
|---|---|---|
| `POOL_FACTOR` | 1.5 | candidate over-provision (runtime lever) |
| `ENABLED_SOURCES` | `google_maps,npi,hotfrog` | YP/Yelp off until proxies |
| `ENABLE_PITCH_RESEARCH` | False | turn on local-LLM pitch step |
| `OLLAMA_URL` / `OLLAMA_MODEL` | localhost:11434 / qwen2.5:7b-instruct-q4_K_M | LLM backend |
| `VERIFY_MIN_SCORE` | 40 | one of three "verified" paths |
| `TOR_SOCKS_PORTS` | 9050,9150,9250,9350,9450 | multi-IP Tor |
| `EXTERNAL_SOCKS_PROXIES` | (unset) | residential proxies — the real volume unlock |

---

## 4. Scaling reality (decided this session)

- **Tiered research** (chosen): scrape a wide funnel, score, deep-research only the
  delivered leads. 10k *researched*/hour is server-class, not laptop-class.
- The code already scales horizontally (Celery). 10k/hour = infra (residential
  proxies + more workers + faster/hosted LLM), **not** a rewrite. A single M1 Air
  tops out around a few hundred/hour.

---

## 5. Known issues NOT yet addressed (next levers)

- **Residential proxies** — drop creds in `EXTERNAL_SOCKS_PROXIES`; unblocks YP/Yelp/
  Hotfrog + medical-site email crawls. Biggest single quality/volume lever.
- **Prod hardening** (untouched): no auth/rate-limiting, CORS `*`+credentials,
  SQLite write-lock under Celery concurrency (no WAL/busy_timeout), no real
  migrations (create_all + SQLite-only auto-ALTER), **Docker runtime stage missing
  Playwright browsers** (scraper crashes in-container as built).
- **Frontend WS host bug**: `frontend/src/utils/api.ts::getWsUrl` builds from
  `location.host`, ignoring `NEXT_PUBLIC_WS_URL` → WS silently falls back to polling
  in split dev. Plus frontend↔backend type drift (`Job.total_leads` etc. don't exist
  in API responses).
- Repo hygiene: `frontend_backup/` (committed legacy), stale `venv/` (262M) beside
  the active `.venv/`.

---

## 6. How to run / submit a job

```bash
# stack (each background): redis up, then —
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
.venv/bin/python -m celery -A backend.workers.celery_app worker --loglevel=info --concurrency=2
cd frontend && npm run dev
# (optional) Tor multi-IP: ./scripts/start_tor_instances.sh 5
# (optional) LLM: ollama serve  (+ ENABLE_PITCH_RESEARCH=true)

curl -s -X POST http://127.0.0.1:8000/api/jobs -H "Content-Type: application/json" \
  -d '{"niche":"Medical clinic","location":"Los Angeles, CA","country":"us","limit":200}'
curl -s http://127.0.0.1:8000/api/jobs/<id>          # poll
# exports: exports/<readable-folder>/{base}.xlsx, {base}.pdf, manifest.json
```

`celery` binary is NOT in `.venv/bin` — invoke via `python -m celery`.

---

## 7. Key files

- Pipeline + scoring: `backend/workers/pipeline.py`
- Sources orchestrator/dedupe/tiling: `backend/services/sources.py`
- Maps discovery (scroll): `backend/services/google_maps_search.py`
- Contact/email/owner: `backend/services/contact_finder.py`, `owner_contact.py`
- Business intel: `backend/services/business_intel.py`
- Pitch research (LLM): `backend/services/pitch_research.py`
- Proxy/Tor: `backend/services/proxy_rotator.py`, `scripts/start_tor_instances.sh`
- Exports: `backend/services/xlsx_builder.py`, `pdf_builder.py`, naming in `backend/utils/naming.py`
- Job API: `backend/api/jobs.py`; data coverage/gaps: `docs/data-sources.md`

---

## 8. 2026-06-29 session — 5 free quality improvements + pipeline run

### Changes implemented

| Area | What | Files changed |
|---|---|---|
| **Tor 10 instances** | `start_tor_instances.sh` defaults to 10, 60s circuits, unique exit IPs | `scripts/start_tor_instances.sh`, `.env` (`TOR_SOCKS_PORTS`/`TOR_CONTROL_PORTS`) |
| **Tor IP verifier** | Standalone script to check all instances | `scripts/verify_tor_ips.py` (NEW) |
| **Hotfrog rotation fix** | Circuit breaker resets on each IP rotation (fresh IP = fresh try), `rotate=True` every page | `hotfrog/hotfrog_core.py:_fetch_page`, `backend/services/sources.py:_gather_hotfrog` |
| **Email verifier** | Format check → MX DNS (cached 5min TTL) → SMTP banner, composite score (0-10), wired into pipeline scoring (+0-10 pts) | `backend/services/verifier.py` (NEW), `backend/workers/pipeline.py:_stage_score` |
| **Phone verifier** | libphonenumber validation + type detection (mobile/voip = +2), area-code→state map (all 50 US states) | `backend/services/verifier.py`, `backend/workers/pipeline.py:_stage_score` |
| **SQLite WAL mode** | `PRAGMA journal_mode=WAL` + `busy_timeout=5000` on both async/sync engines | `backend/database.py` |
| **Repo hygiene** | Removed `frontend_backup/` (84K), stale `venv/` (262M), all `__pycache__`, `.pyc`, `.DS_Store` | — |

### Tests added
- `tests/test_verifier.py` — 19 tests (email format, MX, SMTP, phone, area-code, batch)
- `tests/test_wal_concurrency.py` — 3 tests (WAL mode, busy_timeout, 10-thread concurrent writes)
- `tests/test_hotfrog_rotation.py` — 3 unit tests + 1 live test (skipped by default)

Full backend suite: **26/26 pass** (1 skipped = live Hotfrog).

### Hotfrog live results (post-fix)
| Test | Pages OK | Results |
|---|---|---|
| Miami, FL | 1-6 (7-8 blocked) | 46 results |
| Los Angeles, CA | 1-4 (5-6 blocked) | 35 results |
| Manchester, NH | 1-4 (5-6 blocked) | 32 results |
| Honolulu, HI | 0 (blocked immediately) | 0 from Hotfrog |
| Newark, NJ | TBD | TBD |

Hotfrog works with rotation but exit-IPs get burned across tiles/jobs. Residential proxies would unblock this fully.

### Pipeline status (09 states, Construction, 200 limit)
- Stack: Uvicorn :8000 + Celery (concurrency=4) + 10 Tor + Redis
- 9 jobs submitted 2026-06-29 ~14:20
- Processing in-progress (~30-50 min/job through scraping)
- First results expected ~15:00-15:30

### Config
```env
TOR_SOCKS_PORTS=9050,9150,9250,9350,9450,9550,9650,9750,9850,9950
TOR_CONTROL_PORTS=9051,9151,9251,9351,9451,9551,9651,9751,9851,9951
ENABLED_SOURCES=google_maps,npi,hotfrog
IP_ROTATE_EVERY_N_REQUESTS=3
```

### Known remaining gaps
- Hotfrog still 403s after ~4-6 pages per Tor IP (need residential proxies for full throughput)
- tiled scrape doesn't report per-tile progress to DB (scraped=0 until all tiles finish)
- No state contractor-license lookup (construction; 50 boards, high-effort)
- Yelp/YellowPages disabled until residential proxies

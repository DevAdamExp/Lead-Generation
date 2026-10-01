# Architecture — what every part of this repo does

A map of the codebase: the request path, the job pipeline, every module's job,
and where the bodies are buried. Derived from the actual import graph, not from
memory.

---

## 1 · The two processes

Nothing in this system is a single program. There are **two long-lived
processes** plus a browser fleet, talking through Redis and one SQLite file.

```
   HTTP ─────► FastAPI (uvicorn)                 Celery worker
               backend/main.py                   backend/workers/celery_app.py
                 │                                    │
                 │ POST /api/jobs                     │ pipeline.run(job_id)
                 │ (enqueues SYNCHRONOUSLY)           │
                 ▼                                    ▼
               ┌──────────── Redis ────────────┐   scrape → enrich → score
               │ broker + progress hash        │   → gate → LLM → export
               └───────────────────────────────┘        │
                 ▲                                      │
                 │ WS /ws/jobs/{id} polls progress      ▼
                 │                                 exports/<job>/…xlsx |.pdf
               ┌──────────── SQLite ───────────┐
               │ jobs · leads · delivered_…    │
               └───────────────────────────────┘
```

Why it matters: the API never scrapes. If the worker is down, jobs are accepted
and never move — which is exactly the failure that left six jobs frozen for 16
days, and why enqueue is now synchronous and startup reconciles stale rows.

---

## 2 · Request path (FastAPI)

| Module | Responsibility |
|---|---|
| `backend/main.py` | App factory, CORS, static mount, **startup reconciliation** of stale jobs |
| `backend/api/jobs.py` | Create / list / get / export / **delete** a job. Enqueue is synchronous so a dead broker returns 503 instead of a phantom PENDING row. Delete also removes the export folder. |
| `backend/api/leads.py` | Paginated, filterable lead query |
| `backend/api/ws.py` | Progress stream, reading the Redis hash the worker writes. Bounded — it used to spin forever on a dead job. |
| `backend/api/health.py` | DB / Redis / proxy health. Verdict computed over service keys only. |
| `backend/schemas.py` | Pydantic contracts. `LeadResponse` exposes **all 77** `Lead` columns; a test enforces that so a new column can't be silently hidden. |

---

## 3 · The pipeline (`backend/workers/pipeline.py`, ~1.2k lines)

One Celery task, six stages. This is the heart of the system.

```
run_pipeline(job_id)
 │
 ├─ 1 SCRAPE      _stage_scrape ─► services/sources.gather_leads_tiled
 │                 · 10 query tiles, 4 concurrent, each its own Tor circuit
 │                 · cross-run dedup excludes already-delivered businesses
 │                 · location filter drops out-of-state results
 │                 · RESUME: reuses the pool if a retry finds leads already there
 │
 ├─ 2-5 ENRICH    _enrich_until_target — batches, best-first, stops at target
 │    │            · _stage_maps_plan/fetch/apply  (browser, runs OFF-thread)
 │    │            · _stage_websites               (liveness, SSL, CMS, socials)
 │    │            · _stage_intel_and_contacts     (12-wide pool; ONE page cache
 │    │              shared by business_intel + contact_finder)
 │    │            · _llm_extract_gaps             (optional, fills regex misses)
 │    └─           · _stage_score                  (rubric → lead_score, confidence, tier)
 │
 ├─ 6 GATE        select_export_leads — verified → accurate → contactable,
 │                 every gate with a never-deliver-nothing fallback
 │
 ├─ 7 DEPTH       _stage_reviews (off), _stage_registration ∥ _stage_pitch_research
 │
 └─ 8 EXPORT      _stage_export → XLSX + PDF + manifest, then _record_delivered
```

**Failure handling is part of the design, not an afterthought:**

- `SoftTimeLimitExceeded` is caught **before** the generic handler and exports
  what already cleared the gate — 2 of 9 observed runs exceeded the 120-min limit
  and used to deliver nothing at all.
- `acks_late=True` means a killed worker's task is redelivered; the resume logic
  makes that cheap.
- `reconcile_stale_jobs()` fails jobs whose heartbeat has gone cold, so nothing
  sits RUNNING forever.

---

## 4 · Services — one job each

### Discovery (finding businesses)

| Module | Notes |
|---|---|
| `sources.py` | Orchestrator. Merges every source, resolves field conflicts by per-field source priority, dedups. Owns `run_source` (per-source deadline) and `location_matches` (drops out-of-state results). |
| `google_maps_search.py` | Primary source. Scrolls the feed, then extracts place details **N-wide**, each worker on its own circuit. |
| `npi_source.py` | Official US healthcare registry. Free, keyless, high-volume — carries medical niches. |
| `hotfrog/hotfrog_core.py` | Directory scraper behind Cloudflare. Low yield; the per-source deadline exists largely because of it. |
| `yelp_scraper.py`, `yellowpages_scraper.py` | Off by default — ~0 yield over Tor at the highest cost. |

### Enrichment (learning about them)

| Module | Notes |
|---|---|
| `google_maps_scraper.py` | Confirms a known business on Maps; attaches nothing rather than wrong data on a name mismatch. |
| `website_checker.py` | Liveness, SSL, CMS, socials. SSL check runs via `asyncio.to_thread` — inline it froze the event loop and made the semaphore decorative. |
| `business_intel.py` | Services, team, year founded, partners, marketing stack, platform dependence. Pure regex/BeautifulSoup. |
| `contact_finder.py` | Emails and owner names. Owns the junk-email and person-name gates that keep page furniture out of the export. |
| `owner_contact.py` | Best-effort owner phone; one cached RDAP lookup shared with `business_intel`. |
| `verifier.py` | Email MX/RCPT, phone via libphonenumber, address via the Census geocoder. Host-level SMTP kill switch when port 25 is blocked. |
| `review_scraper.py` | Review text/histogram. **Distinguishes "throttled" from "no reviews"** so a soft-block isn't recorded as a fact. |
| `registration.py` | Legal-entity lookup (off by default). |

### Intelligence & output

| Module | Notes |
|---|---|
| `pitch_research.py` | Per-lead pitch synthesis over already-scraped fields. NVIDIA NIM with model fallback, rate limiter, dead-model memo, truncation salvage. |
| `lead_dossier.py` | Per-lead research **dossier PDF** — the 5-section document. Entry point: `scripts/build_dossiers.py`. |
| `vertical_profiles.py` | Per-niche framing for the pitch prompt, encoded from `docs/verticals/*.md`. |
| `xlsx_builder.py`, `pdf_builder.py` | The deliverables. |
| `proxy_rotator.py` | Tor pool. Proves a port is **working Tor** before adopting it, gives each worker its own circuit deterministically. |

---

## 5 · Things that will bite you

Hard-won, all reproduced live:

- **`TOR_SOCKS_PORTS` is a list of candidate ports, not a claim they are Tor.**
  An SSH tunnel on `:10250` was adopted as a proxy and black-holed every scrape.
  `circuit_works()` now demands `IsTor: true`.
- **`build_tiles` invents real places.** "Bend, OR" expands to "South Bend, OR",
  which Google resolves to South Bend **Indiana**. Hence `location_matches`.
- **`ThreadPoolExecutor` cannot abandon work** — its atexit hook joins workers, so
  a thread stuck in a socket blocks interpreter shutdown. Use
  `utils.retry.call_with_deadline` (daemon thread) for anything abandonable.
- **A SQLAlchemy `Session` is not thread-safe.** Stages that run off-thread do
  network work only and return results; all ORM writes happen on the main thread.
- **`docs/*/` Obsidian vaults contain `.canvas` files with hard-coded paths.**
  Renaming those folders silently breaks them.
- **Google throttles by IP.** Symptoms are subtle: a place panel renders a rating
  but no review count, or Maps returns 0 for every tile. Not a selector problem.

---

## 6 · Entry points

| Command | Does |
|---|---|
| `uvicorn backend.main:app` | API |
| `celery -A backend.workers.celery_app worker -c 1` | Pipeline worker (concurrency 1 — see the rate-limiter note in `celery_app.py`) |
| `python scripts/build_dossiers.py --latest` | Research dossier PDF per delivered lead |
| `python scripts/e2e_lifecycle_test.py <job_id>` | Full lifecycle test + PDF report |
| `./scripts/start_tor_instances.sh 6` | Start Tor pool (~3 min to bootstrap) |
| `python scripts/verify_tor_ips.py` | Confirm distinct exit IPs |
| `pytest` | Whole suite (one test root, configured in `pyproject.toml`) |

---

## 7 · Data model

`backend/models.py` — three tables:

- **`jobs`** — one search. Carries status, progress, `heartbeat_at` (liveness for
  the reconciler) and export paths.
- **`leads`** — 77 columns: scraped facts, enrichment, verification flags,
  scoring, and LLM output. Cascade-deleted with its job.
- **`delivered_businesses`** — deliberately **no foreign key**, so "we already
  sold this business" survives job deletion. A test asserts the absence of the FK,
  because adding one would silently reopen double-delivery.

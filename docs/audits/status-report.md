# Status Audit & Procedure Report

_Date: 2026-07-03. Audit of the 5 goals against the actual code (not the roadmap)._
_Updated 2026-07-03 (evening): implementation pass — see "Implementation log" at the bottom._

**Legend:** ✅ done end-to-end · 🟡 partially done · 🔴 not done

---

## Summary Table (post-implementation)

| # | Goal | Status | What's left |
|---|------|--------|-------------|
| 1 | NVIDIA free LLM for deep research | ✅ hardened | Dead-model memoization + chain refresh + single-worker rate budget done. Still wants one real end-to-end run to confirm on live keys. |
| 2 | Make lead-gen as fast as possible | ✅ core done | Parallel Maps tiles, Hotfrog/NPI hoisted to once-per-job, parallel Maps enrichment, email verify cached (no serial SMTP in scoring). Needs a timed before/after run to quantify. |
| 3 | Hotfrog: all leads in one search | 🟡 posture fixed | IP-per-page anti-pattern removed (warm session, rotate only on block). The "all leads in one search" sitemap/cf_clearance/Common-Crawl harvest is **left as documented next work** — it needs live Cloudflare probing to build safely, not blind code. |
| 4 | Depth: reviews, company details, registration | ✅ built (gated) | Real review-tab scraping + LLM themes + evidence-based weaknesses (4a); SAM.gov/OpenCorporates registration lookup (4b). Both off by default; live selectors/keys need one validation run. |
| 5 | Accuracy ≥ 95% | ✅ machinery done | RCPT+catch-all email verify, verification_tier, Census address check, phone corroboration, honest 0.6 gate. The measurement *loop* (sample→score→tighten) is the remaining human process, not code. |

---

## 1. NVIDIA Free LLM Deep Research — 🟡 mostly done

### What exists (verified in code)
- `backend/config.py:62-82` — provider `auto` (NVIDIA when key present), fallback chain
  `nemotron-super-49b → llama-3.3-70b → qwen2.5-7b`, `PITCH_CONCURRENCY=6`.
- `backend/services/pitch_research.py` — OpenAI-compatible NIM call, 36 rpm token-bucket
  limiter, per-lead soft-fail, lenient JSON parsing, parallel ThreadPool.
- `.env` has `NVIDIA_API_KEY` set and `ENABLE_PITCH_RESEARCH=true`.
- Pitch fields flow into the XLSX (`xlsx_builder.py:75-77`). End-to-end path is wired.

### What's remaining
1. **Dead-model retry cost.** `_call_nvidia` retries the full model chain **per lead**. If
   model #1 is deprecated/down, every one of 200 leads burns a rate-limited call + timeout
   on it first.
2. **Rate limiter is per-process.** `_NVIDIA_LIMITER` is a module global — one Celery worker
   process. Run 2+ workers (or worker + manual script) and the combined rate exceeds 40 rpm
   → 429s.
3. **Model chain is hardcoded taste, not verified.** Which models are actually "long-lasting"
   on the free tier changes; nothing checks the chain against build.nvidia.com.
4. **No proof it works end-to-end** on a real job (only unit tests).

### Procedure
1. **Verify & pin the model chain** (30 min, repeat quarterly):
   - `curl https://integrate.api.nvidia.com/v1/models -H "Authorization: Bearer $NVIDIA_API_KEY"`
     and confirm each model in `NVIDIA_MODELS` is listed.
   - Selection rule for longevity: prefer **first-party flagship instruct models**
     (`meta/llama-3.3-70b-instruct`, `meta/llama-3.1-8b-instruct` as the fast fallback,
     `qwen/qwen2.5-7b-instruct`) over NVIDIA fine-tunes (nemotron variants rotate more often).
     Put the 70B first (quality), 8B/7B last (survivability). 3 models is enough.
2. **Memoize dead models for the process lifetime**: on a non-429 failure, add the model to a
   module-level `_DEAD_MODELS` set; skip it for subsequent leads. Reset per run. (~5 lines.)
3. **Serialize the rate budget across workers**: either (a) run pitch research only in the one
   pipeline worker (document `celery -c 1` for this queue), or (b) move the limiter to Redis
   (`INCR` + `EXPIRE` on a per-minute key). (a) is the lazy correct answer today.
4. **Validation run**: one real job with `limit=10`, then check
   `sqlite3 leads.db "SELECT name, pitch_angle IS NOT NULL, pain_points IS NOT NULL FROM leads ORDER BY created_at DESC LIMIT 10"`
   — expect ≥ 9/10 populated and openers that cite a concrete lead fact.

---

## 2. Speed — 🟡 the big levers are still on the table

### What exists (verified in code)
- Verified-target design: over-scrape pool (`POOL_FACTOR=1.5`), enrich best-first in batches
  of 50, stop at target (`pipeline.py:_enrich_until_target`).
- Intel + contacts merged into one ThreadPool (10-wide); website checks 8-wide.
- YP/Yelp off by default (they burned time for ~0 yield over Tor); Maps batch scraper reuses
  one browser.

### What's remaining — the actual bottlenecks, in order of cost
1. **Tiles run strictly sequentially** (`sources.py:gather_leads_tiled` — a plain `for` loop
   over up to 10 tiles; each tile = Maps search + Hotfrog crawl + NPI, also sequential
   inside `gather_leads`). Scrape wall-clock ≈ sum of everything.
2. **Hotfrog is crawled per tile** — `gather_leads` calls `_gather_hotfrog` every time, so one
   job re-crawls Hotfrog with near-identical queries up to 10×, each crawl paying
   per-page IP rotation + 1.5 s delay + (when blocked) a 40–60 s Playwright challenge attempt.
3. **Maps enrichment is sequential per lead** inside the batch scraper (one browser, one
   lookup at a time), for every non-Maps-sourced lead.
4. **SMTP/MX verification runs serially inside `_stage_score`** (`pipeline.py:605-612`) — a
   blocking 3 s-timeout network call per emailed lead, in a plain loop, after the parallel
   enrich pool has already finished. 50 emails ≈ up to 2.5 min of dead serial time per batch.

### Procedure
1. **Parallelize tiles** (biggest win, ~3-4× on scrape): run `gather_leads` for 3–4 tiles
   concurrently via `ThreadPoolExecutor`, merging into the pool under a lock (or merge after
   each future completes on the main thread — `_merge_pool` is already the merge primitive).
   Keep the early-exit: stop submitting tiles once the pool ≥ target.
2. **Hoist Hotfrog (and NPI) out of the tile loop**: scrape each once per job with the base
   query, merge into the pool once. Tiles then only vary the Maps query — which is the only
   source where tiling adds coverage. (NPI already fetches 150+ in one sweep; per-tile calls
   are pure waste.)
3. **Parallelize Maps enrichment**: 2–3 browser **contexts** in the existing single browser
   (contexts are cheap; a second browser is not), each pulling from a shared queue of leads.
4. **Move email verification into the enrich pool**: call `verify_email` inside
   `_stage_intel_and_contacts`'s `_enrich` (it already runs 10-wide) and stash the result on
   the lead; `_stage_score` then reads the cached result instead of doing network I/O.
   Also add a module-level result cache keyed by email domain (MX is already cached; the
   SMTP banner check isn't).
5. **Measure before/after**: time a fixed job (`limit=50`, same niche/city) and record
   per-stage timings in the manifest (`counts` sibling: `"timings": {...}`). Target:
   50 verified leads in < 15 min without proxies-related blocks.

---

## 3. Hotfrog — full-niche extraction, bypassing pagination & rate-limiting — 🔴 not done

### What exists
`hotfrog/hotfrog_core.py` + `sources.py:_gather_hotfrog`: path-suffix pagination (`/2`, `/3`…),
~12 results/page, IP rotation every page, circuit breaker on hard block, Playwright challenge
solving as fallback. This is the *opposite* of "all leads in one search" — it's many small,
expensive, blockable requests.

### Live probe results (done during this audit, 2026-07-03)
- `https://www.hotfrog.com/robots.txt` → **200, served without challenge**. It reveals
  `Sitemap: https://www.hotfrog.com/sitemap-index.xml` and disallows `/search/ankh/`
  (an internal search endpoint worth inspecting once past the challenge).
- `sitemap-index.xml` and `/search/us/...` → **Cloudflare managed JS challenge (403)** for
  plain HTTP clients, even from a residential IP. So the challenge, not the IP, is the wall;
  Tor only makes it worse.

### Procedure — three approaches, in the order to try them

**A. Sitemap enumeration with a harvested `cf_clearance` cookie (primary — true "no pagination")**
1. Open `sitemap-index.xml` once in Playwright (existing challenge-solving code), wait for
   the challenge to pass.
2. Extract the `cf_clearance` cookie + the exact User-Agent from that browser context.
   Cloudflare binds clearance to **IP + UA**, typically valid 30+ minutes.
3. Reuse cookie+UA in plain `httpx` (fast, no browser) to download the sitemap index, then the
   child sitemaps. Directory sitemaps are usually segmented by state/category and contain
   **every `/company/...` profile URL on the site** — the complete business universe, zero
   pagination, zero search.
4. Filter URLs by location/category slug (Hotfrog profile URLs embed city/state), then fetch
   only the matching profiles with the same cookie session at modest concurrency (4–6,
   ~0.5 s pacing — one warm session is far less suspicious than rotating IPs). Parse with the
   existing JSON-LD parser (`_parse_details_page` already works).
5. When the cookie dies (challenge HTML detected — `_looks_like_challenge` already exists),
   re-solve once in Playwright and continue. One challenge per ~30 min instead of one per page.
6. Cache the URL universe per (state, category) on disk — it changes slowly; re-fetch weekly.
   After the first run, a "search" is a local file filter + profile fetches only for new URLs.

**B. Internal JSON API discovery (if A's sitemaps turn out truncated)**
1. In Chrome DevTools on a Hotfrog search page, watch the Network tab for `fetch/XHR` calls
   (and check for `__NEXT_DATA__` / `.json` route payloads — modern directories ship the
   result list as JSON).
2. If found, replay that endpoint with the `cf_clearance` session and probe its paging params
   (`size`, `limit`, `rows`) — APIs often accept 100+ per request even when the UI shows 12.
   Also probe the robots-disallowed `/search/ankh/` path — disallowed usually means "real
   endpoint they don't want indexed".

**C. Common Crawl / Wayback (zero-rate-limit fallback, fully offline)**
1. Query the Common Crawl index (`index.commoncrawl.org`, free, no auth) for
   `www.hotfrog.com/company/*` — returns every crawled profile URL + WARC pointers.
2. Download the WARC slices for matching pages and run the existing JSON-LD parser on the
   stored HTML. No requests to Hotfrog at all → no rate limiting, no challenge, at the cost
   of data being weeks–months stale (fine for name/phone/address; re-verify via Maps anyway).
3. Wayback CDX API (`web.archive.org/cdx/search/cdx?url=hotfrog.com/company/*`) is the same
   idea with different coverage; use whichever has more of your niche.

**Rate-limit posture for all three:** one warm session > many rotating ones. Keep the
circuit breaker, but rotate the *session* (re-solve challenge), not the IP per page.

---

## 4. Per-Lead Depth — reviews, company details, registration — 🟡 half done

### What exists (verified in code)
- `business_intel.py` (986 lines): long description, employee count, year founded (incl. RDAP
  domain age), partners/clients, services/menu, team members + owner title, marketing stack /
  ad pixels, external platforms (DoorDash/Booking dependence), obfuscated-email decoding.
- Review **signals**: `review_velocity` (reviews ÷ years) and `_compute_review_weaknesses` —
  but these are *derived from rating + count only*. The code's own comment admits it:
  `"velocity, not true recency. Real last-review date needs scraping the reviews tab"`.
- All existing depth fields flow to model + XLSX.

### What's remaining
1. **No actual review content.** Nothing reads a single review text. "Review weaknesses" like
   "Mixed reviews (rating < 3.5)" are arithmetic, not evidence. No complaint themes, no
   last-review date, no owner-response rate.
2. **Zero registration data.** No state Secretary-of-State lookup, no SAM.gov, no
   OpenCorporates, no legal-entity name/type/status/officers/registration date. (`grep` for
   any of these: only hits are a regex word and a roadmap wish.)
3. Review platforms beyond Google (Yelp/Facebook ratings) aren't captured since those sources
   are disabled.

### Procedure

**4a. Real review depth (Google Maps reviews tab)**
1. Extend the existing Maps batch scraper: for leads with `google_maps_url` and
   `review_count > 0`, click the Reviews tab, apply **sort = "Lowest rating"**, harvest the
   first ~10 review texts + dates + star values (one scroll; no need for all reviews —
   lowest-first *is* the weakness signal).
2. Grab from the same pane for free: rating **histogram** (5★…1★ counts), **last review
   date** (true recency — replaces the velocity proxy), and **owner-response presence** on
   negative reviews (no response = reputation-management wedge).
3. Theme extraction: pass the ≤10 negative texts to the already-built NVIDIA call in the
   pitch-research stage (one extra field in the same prompt/response JSON:
   `"review_themes": [...]` — costs zero extra requests).
4. New columns: `review_lowest_texts` (JSON), `last_review_date`, `rating_histogram`,
   `owner_responds` — wire into scoring (real weaknesses replace `review_weakness_count`)
   and XLSX.
5. Cost control: do this only for the export slice (≤ `limit` leads), not the whole pool.

**4b. Registration / legal-entity data (the true "end-to-end company details")**
1. **SAM.gov Entity Management API** — free key, 1 000 req/day: legal business name, UEI,
   registration status, address, NAICS. Only covers businesses registered for federal work,
   so treat as bonus corroboration.
2. **State Secretary-of-State lookups** — the real registry. No single free national API, so:
   start with the 5 states you actually sell into. CA, NY, FL, TX, CO all have free search
   endpoints (some JSON, some HTML). Build one `sos_lookup(name, state)` per state behind a
   common interface; extract entity name, number, status (active/dissolved), registration
   date, registered agent. Derive `state` from the already-computed area-code→state mapping.
3. **OpenCorporates** as the fallback aggregator (free tier ~500 req/mo — reserve it for
   leads the state scrapers miss).
4. New columns: `legal_name`, `entity_type`, `entity_status`, `registration_date`,
   `registered_agent`, `registry_source`. Feed `entity_status == active` into
   `data_confidence` (+0.10) and dissolved → hard reject (directly serves goal 5).
5. Order of work: 4a first (every lead benefits, one scraper you already own), then SoS for
   top-2 states, then SAM.gov, then OpenCorporates.

---

## 5. Accuracy ≥ 95% — 🟡 gates exist, but unproven and one is broken

### What exists
- `verifier.py`: email format→MX (cached)→SMTP banner; phone via libphonenumber;
  `data_confidence` 0–1 rubric; `_is_accurate` export gate
  (`MIN_EXPORT_CONFIDENCE=0.5` + verified contact or Maps + 2 sources); cross-run dedup.

### What's remaining
1. **Confirmed bug — `email_verified` can never be True.**
   `contact_finder.py:637` does `verify_email(...).get("verified", False)`, but
   `verify_email` returns `format_valid/mx_valid/smtp_valid/...` — there is **no `"verified"`
   key**. Every lead ships `email_verified=False`, so the accuracy gate silently loses one of
   its two strong signals and email-only leads can never pass `_is_accurate` via email.
2. **The fallback delivers unverified leads with no marking.** `pipeline.py:182` —
   `accurate_pool or verified_pool` (and further fallbacks down to raw `leads`) means when
   the gate filters hard, the export silently backfills with leads that failed it. Necessary
   to never ship empty files, but fatal to a 95% *guarantee* unless flagged.
3. **SMTP banner ≠ mailbox exists.** Current check connects to the MX and reads EHLO — it
   proves the *domain* accepts mail, not the *address*. Catch-all domains pass everything.
4. **`phone_verified` = "number is well-formed for the region"**, not "line is active".
5. **No measurement.** Nothing samples delivered leads against ground truth, so "95%" is
   currently an aspiration with no number attached.

### Procedure
1. **Fix the bug (one line):** map `email_verified` to `mx_valid` (or `score >= 6`) from the
   actual `verify_email` result. Add a regression test asserting the key exists.
2. **Upgrade email check to RCPT-TO probe:** after EHLO, send `MAIL FROM:<probe@yourdomain>`
   + `RCPT TO:<lead-email>` and read the code (250 = deliverable, 550 = dead) — **never**
   DATA, so no email is sent. Detect **catch-alls** by also probing a random address at the
   same domain: if the random one gets 250 too, mark `email_verified="catch_all"` (counts as
   MX-level confidence only). Cache per domain; rate-limit to a few probes/domain; skip
   gracefully when port 25 is blocked (many ISPs) — fall back to MX-only and record which
   check actually ran.
3. **Phone:** no free line-liveness check exists — don't pretend. Instead raise confidence by
   corroboration: phone matches across ≥ 2 independent sources (Maps + NPI/Hotfrog/SoS) →
   treat as verified; single-source phone → `phone_verified=false` regardless of format
   validity. (The merge layer already tracks per-source values — currently discarded after
   merge; keep the per-field source agreement count.)
4. **Address validation (free):** US Census Bureau geocoder (`geocoding.geo.census.gov`, no
   key, no hard limit) — a matched, standardized address is +confidence; unmatched is a flag.
5. **Make the gate honest:**
   - Raise `MIN_EXPORT_CONFIDENCE` to 0.6 (the config comment already says this is the
     >95% posture).
   - Keep the never-empty fallback, but add a `verification_tier` column
     (`verified` / `corroborated` / `unverified-fallback`) and a highlighted band in the
     XLSX. The 95% claim then applies to the `verified` tier, checkably.
6. **Measure it (this is the actual 95% work):**
   - After each job, sample 20 delivered leads. Verify each on 3 axes: phone (matches an
     independent live source — Maps listing today), website (loads + business name on page —
     already computed as `website_name_found`), name/address (Maps or SoS agreement).
   - Automate the check as a post-export script scoring `correct_fields / checked_fields`;
     write `"accuracy_sample": {...}` into `manifest.json`.
   - Iterate: run → measure → find the failing field class → tighten that extractor → repeat
     until the sampled rate holds ≥ 95% across 3 consecutive jobs. Without this loop, no
     config value can honestly claim 95%.

---

## Recommended execution order

1. **5.1** — the `email_verified` one-line bug fix (broken today, blocks everything else in goal 5).
2. **2.1–2.4** — speed (parallel tiles, hoist Hotfrog/NPI, parallel Maps, async email checks): pure wall-clock win, no new data needed.
3. **3.A** — Hotfrog sitemap + `cf_clearance` harvesting: turns the worst source into the widest one.
4. **4a** — real review scraping: biggest depth win, reuses the Maps browser you already have.
5. **5.2–5.6** — RCPT probing, corroboration rules, tiers, and the accuracy measurement loop.
6. **4b** — registration data (SoS/SAM.gov): most new-code effort, so last; also feeds accuracy once present.
7. **1.1–1.4** — NVIDIA hardening: small, do whenever a run shows 429s or a dead model.

---

## Implementation log (2026-07-03 evening)

What was actually changed in code, by goal. All 425 unit tests pass (10 new).

### Goal 5 — accuracy
- **`email_verified` "bug" was a false alarm.** The audit claimed it was always
  False; in the current tree `contact_finder` imports `verify_email` from
  `owner_contact` (which *does* return a `verified` key), so it worked at MX
  level. The real defect was two competing `verify_email`s and a *second* serial
  SMTP re-check inside scoring. Fixed by unifying on one function.
- `verifier.verify_email` upgraded: **RCPT-TO probe** (never DATA — no mail sent)
  + **catch-all detection** (random-address probe) + **port-25-blocked fallback**
  to MX-only. Returns `deliverable`/`catch_all`/`verified`/`tier`. **Per-address
  result cache** → the scoring stage's re-verify is now a free cache hit (this is
  also the fix for speed item 2.4). Deleted the duplicate `owner_contact.verify_email`.
- `verification_tier` column (`verified` / `corroborated` / `unverified`) computed
  in scoring, rendered as a colour-banded column + summary counts in the XLSX.
- **Census address validation** (`verify_address`, free, keyless) wired into the
  enrich pool; feeds `data_confidence`.
- **Phone corroboration**: `phone_source_count` (independent sources agreeing on
  the phone) preserved through the merge and fed into confidence. Did **not** demote
  single-source Maps phones (a valid Maps phone is authoritative — demoting it would
  *hurt* the label's accuracy).
- Export gate raised to `MIN_EXPORT_CONFIDENCE = 0.6`; never-empty fallback kept,
  now honestly labelled via the tier column.

### Goal 2 — speed
- **Parallel Maps tiles** (`TILE_CONCURRENCY=4`) with early-exit at target.
- **Hotfrog + NPI hoisted to once-per-job** (was re-crawled per tile, up to 10×).
- **Parallel Maps enrichment** (`MAPS_ENRICH_CONCURRENCY=2` browser contexts).
- Serial SMTP in scoring eliminated via the verify cache (see goal 5).

### Goal 1 — NVIDIA
- **Dead-model memoization** (`_DEAD_MODELS`): a non-429 4xx marks a model dead so
  later leads skip it; a 429 does not (transient). Model chain reordered to
  first-party flagships (70B → 8B → qwen). Documented `celery -c 1` + set
  `worker_concurrency=1` so the per-process rate limiter isn't doubled.

### Goal 4 — depth (built, gated off by default)
- **4a `review_scraper.py`**: opens the Maps reviews tab, sorts lowest-first,
  harvests texts/stars/dates/histogram/owner-response; evidence-based weaknesses
  replace the arithmetic proxy; **review themes** extracted via the *existing*
  NVIDIA pitch call (one extra JSON field, zero extra requests). Runs on the export
  slice only. `ENABLE_REVIEW_SCRAPE`.
- **4b `registration.py`**: SAM.gov + OpenCorporates lookups (key-gated), active →
  +confidence, dissolved → reject. Per-state SoS left as a documented one-at-a-time
  extension point (no unified free API; not built speculatively). `ENABLE_REGISTRATION_LOOKUP`.

### Goal 3 — Hotfrog
- Fixed the **rotate-IP-every-page anti-pattern** → warm session, rotate only after
  a block. The full sitemap/`cf_clearance`/Common-Crawl "one search" harvest is
  **intentionally not shipped blind** — it depends on live Cloudflare behaviour that
  must be probed against the real site first. §3 above is the build plan.

### Needs a live run (not unit-testable here)
- Maps **review-tab selectors** (Google's DOM classes rotate — the harvester is
  defensive + soft-fails, but selectors want confirming on a real page).
- Registration lookups need a **SAM.gov key** / OpenCorporates quota to exercise.
- The **accuracy measurement loop** (5.6): sample 20 delivered leads, score
  correct-fields/checked-fields, tighten the weakest extractor, repeat until the
  `verified` tier holds ≥ 95% across 3 jobs. This is process, not code.

# Lead-Generator — Latency & LLM Audit

Date: 2026-07-25. Scope: full backend pipeline, measured against the 9 real runs in `exports/*/manifest.json`.

## 0. Measured baseline (not estimates)

| job | limit | scraped | verified | exported | minutes | sec/candidate |
|---|---|---|---|---|---|---|
| Atlanta GA | 200 | 129 | 90 | 69 | 78.3 | 36 |
| Honolulu HI | 200 | 104 | 82 | 63 | 65.2 | 38 |
| Houston TX | 200 | 43 | 21 | 16 | 147.1 | 205 |
| Los Angeles CA | 200 | 21 | 4 | 2 | 30.9 | 88 |
| Manchester NH | 200 | 113 | 82 | 43 | 62.7 | 33 |
| Miami FL | 200 | 18 | 1 | 1 | 21.1 | 70 |
| Newark NJ | 200 | 33 | 17 | 6 | 65.5 | 119 |
| Philadelphia PA | 200 | 11 | 4 | 3 | 63.5 | 346 |
| Seattle WA | 200 | 61 | 59 | 42 | 154.3 | 152 |

Two distinct failures, often conflated:

1. **Throughput** — pool target is `200 × POOL_FACTOR 1.5 = 300` candidates. Best run got 129. The tiler never early-exits, so **every run pays for all 10 tiles** and still under-delivers.
2. **Latency** — median ~65 min for ~100 candidates. 33–346 s of wall-clock *per candidate*.

Because the pool never fills, `_enrich_until_target` also never breaks early (`pipeline.py:413`) — every scraped candidate gets the full enrich chain. So both stages run at 100% cost, always.

---

## 1. Latency findings, ranked by wall-clock impact

### L1 — `TILE_CONCURRENCY=1` in `.env` serialises the entire scrape
`.env` overrides the code default of 4. `sources.py:444` builds a `ThreadPoolExecutor(max_workers=1)`, so the 10 query tiles from `build_tiles()` run **one after another**, each launching its own Chromium.

Fix: `TILE_CONCURRENCY=4`. Free, config-only, ~4× on the dominant stage.
Caveat: 4 concurrent tiles = 4 Chromium processes (~1.5 GB). If that was why it was set to 1, fix L2 first, which removes the reason.

### L2 — Sequential per-place navigation inside each tile
`google_maps_search.py:471-505`: after collecting up to 60 place URLs, the loop navigates **one page, one place at a time**:

```
page.goto(href, 40s)                 # over Tor: 3–8s
wait_for("h1.DUwDvf", 9000)          # up to 9s
wait_for("[data-item-id=...]", 6000) # up to 6s — full stall on service-area
                                     # businesses that have no address row
sleep(0.6–1.2)
```

60 places × ~5 s ≈ **5 minutes per tile**, and the two `wait_for` timeouts are pure dead air whenever a field is genuinely absent.

Fix: reuse the worker-queue pattern already written in `google_maps_scraper.py:310-367` — N contexts pulling place URLs off an `asyncio.Queue`, each on its own Tor circuit. That code exists and is proven; discovery just doesn't use it. Then drop the second `wait_for` to 2500 ms (it's a nice-to-have, not a gate).

Expected: 4–8× on the largest single cost in the pipeline.

### L3 — `_check_ssl_certificate` blocks the event loop (website stage is secretly serial)
`website_checker.py:179` calls `_check_ssl_certificate(hostname)` from inside `async def _check_single`. That function does a **synchronous** `socket.create_connection(..., timeout=8)` plus a TLS handshake (`website_checker.py:121-122`).

`check_all_websites` gathers all leads under a semaphore of 12 — but a blocking call in a coroutine freezes the *whole* event loop. The semaphore buys nothing: website checking runs at ~1–8 s **per lead, serially**. A 50-lead batch that should take ~30 s takes up to 400 s.

Fix (one line):
```python
lead.has_ssl_info = await asyncio.to_thread(_check_ssl_certificate, hostname)
```
This is the highest impact-to-diff-size item in the audit.

### L4 — `IP_ROTATE_EVERY_N_REQUESTS=3` blocks a worker for 5–35 s at a time
Code default is `0` (off); `.env` sets `3`. Every 3rd `get_proxy()` call takes the auto-rotate branch (`proxy_rotator.py:330-335`), which calls `rotate_ip(force_new=True)` → `rotate_circuit()` → **`time.sleep(5)`** (`proxy_rotator.py:172`) → `get_current_ip()`, which walks up to 3 external endpoints at 10 s each (`proxy_rotator.py:139-153`). Worst case 35 s.

The 30 s cooldown caps this at one rotation per 30 s, so over a 65-min run: ~130 rotations × ~10 s ≈ **20 minutes of blocked worker time**.

Worse: `scrape_maps_ratings_batch._worker` calls `_worker_proxy()` (`google_maps_scraper.py:322`) from inside a coroutine. A rotation there does a blocking `time.sleep(5)` in the event loop and **freezes all N browser contexts at once**.

It's also redundant. Tor's `MaxCircuitDirtiness` already churns circuits, and the per-tag SOCKS-auth isolation (`proxy_rotator.py:115-124`) is what actually gives each worker its own exit IP. Explicit NEWNYM buys nothing on top.

Fix: `IP_ROTATE_EVERY_N_REQUESTS=0`. Keep `report_block()` for the on-block path, where a fresh identity genuinely helps.

### L5 — Health monitor holds the global proxy lock across `sleep(3)` per dead daemon
`proxy_rotator._check_and_restart_dead` (`:608-639`) takes `self._lock` and then, for **each** dead daemon, spawns Tor and `time.sleep(3)`. With 20 configured ports all down, that's the global lock held for **60 seconds**, every 60 seconds.

Every `get_proxy()` takes that same lock (`:340`, `:369`). So all 12 enrichment threads plus the Maps event loop stall for up to a minute per monitor tick, whenever Tor is unhealthy — which correlates exactly with the 147 min / 154 min outlier runs.

Fix: collect the dead instances under the lock, restart them outside it. Or drop the sleep and let the next tick's `is_tor_running()` confirm.

### L6 — `business_intel` and `contact_finder` crawl the same site twice
Both run per lead in `_stage_intel_and_contacts` (`pipeline.py:507-518`), each with its own `httpx.Client`, each crawling its own path list:

- `business_intel.ALL_INTEL_PATHS` (`:65-67`): `/about`, `/about-us`, `/team`, `/our-team`, `/staff`, `/leadership`, … capped at 8 fetches
- `contact_finder.CONTACT_PATHS` (`:185-193`): `/contact`, `/about`, `/about-us`, `/team`, `/our-team`, `/staff`, `/leadership`, … capped at 10 fetches, **plus** 3 sitemap probes (`:201-205`) and the sitemap-discovered pages

Overlap is ~7 paths, plus both fetch the homepage. That's **~8 duplicate HTTP requests per lead with a live site**, at `TIMEOUT` 8–12 s each.

Fix: one `dict[url, html]` fetch cache created per lead in `_stage_intel_and_contacts` and passed into both `_fetch` calls. ~15 lines, roughly halves enrichment network time.

### L7 — RDAP/WHOIS per lead for near-zero yield
Two separate offenders on every lead:

- `business_intel._extract_domain_age` (`:338-370`) queries `rdap.verisign.com/com/v1/...` — **hardcoded to `.com`**. Every `.net`/`.org`/`.io`/`.co` lead 404s, then falls through to `subprocess.run(["whois", domain], timeout=8)`. A blocking subprocess per lead.
- `owner_contact.rdap_registrant_phone` (`:117-155`) hits `rdap.org` with `timeout=12`, for a field that is redacted post-GDPR in nearly all cases.

Fix: use `https://rdap.org/domain/{domain}` for both (it routes by TLD), share one lookup between the two call sites, delete the `whois` subprocess fallback. Or gate `_extract_domain_age` behind "no year found anywhere else", which is already almost true — it's just last in the chain, not conditional on the run being worth it.

### L8 — SMTP port-25 probing has no global kill switch
`verifier.verify_email` does a RCPT probe per address (`:282-299`). `_SMTP_DEAD_DOMAINS` (`:48`) memoizes failures **per domain** — but every lead has a different domain, so on a host where outbound port 25 is blocked (most ISPs, most clouds, macOS dev boxes), you pay a fresh `timeout=3` connect **per lead, forever**, and learn nothing.

Fix: after K consecutive `None` results from `_rcpt_code`, set a module-level `_SMTP_UNAVAILABLE = True` and skip probing for the rest of the process. The `mx_only` tier already handles that degradation path correctly.

### L9 — Stage barriers inside each enrich batch
`_enrich_until_target` (`pipeline.py:399-405`) runs, per 50-lead batch:

```
_stage_maps          (browser, all 50)   ─┐ barrier
_stage_websites      (all 50)            ─┤ barrier
_stage_intel_and_contacts (all 50)       ─┤ barrier
_stage_score         (all 50)            ─┘
```

Nothing overlaps. The slowest lead in each stage gates the next stage for all 50. The Maps stage in particular is a browser launch + N lookups while the 12-thread HTTP pool sits idle.

Fix: `_stage_maps` only touches leads without Maps data, and `_stage_websites`/intel/contacts only need `lead.website`. Overlap Maps with the website+intel pool — they contend for nothing.

### L10 — Fresh `httpx.AsyncClient` per lead
`website_checker.py:248` builds a new client inside the semaphore, per lead. No connection pooling, new TLS handshake each time. Hoist it out of `_guarded` — 2-line change, small but free.

### L11 — Chromium relaunched per tile
`google_maps_search.search_maps_businesses` (`:405-418`) opens `async_playwright()` and launches Chromium on every call, i.e. once per tile. ~2–3 s × 10 tiles ≈ 30 s per job. Minor next to L1/L2, but it's the same fix: hoist the browser to `gather_leads_tiled` and pass contexts down.

---

## 2. Ordered work list — ALL APPLIED 2026-07-25

| # | Change | Files | Status |
|---|---|---|---|
| 1 | `await asyncio.to_thread(_check_ssl_certificate, …)` | `website_checker.py` | ✅ verified 0.51 s vs 4.0 s serial on an 8-lead bench |
| 2 | `IP_ROTATE_EVERY_N_REQUESTS=0` | `.env` | ✅ |
| 3 | `TILE_CONCURRENCY=4` | `.env` | ✅ |
| 4 | Parallel place-detail workers, per-context circuits, 6 s wait → 2.5 s | `google_maps_search.py` | ✅ new `MAPS_DETAIL_CONCURRENCY` (default 4) |
| 5 | Restart daemons outside the lock; one bootstrap wait, not N | `proxy_rotator.py` | ✅ |
| 6 | Shared per-lead page cache for intel + contacts | `pipeline.py`, `business_intel.py`, `contact_finder.py` | ✅ |
| 7 | Global SMTP-unavailable kill switch | `verifier.py` | ✅ |
| 8 | One cached RDAP call via `rdap.org`; `whois` subprocess deleted | `business_intel.py`, `owner_contact.py` | ✅ |
| 9 | Maps stage split plan/fetch/apply, overlapped with website+intel | `pipeline.py` | ✅ |

Also fixed along the way:
- `business_intel` now canonicalises the website URL like `contact_finder` does — it was crawling `google.com/url?q=…` instead of the business for Maps-redirect URLs.
- LLM-1: `.env` `NVIDIA_MODELS` reordered to lead with `meta/llama-3.3-70b-instruct`.
- LLM-2: registration + pitch research now overlap (disjoint field sets), reviews still ordered before pitch research since it feeds the prompt.
- §4: dead `cms_score`/`ssl_score` locals removed; CORS `allow_credentials=False`; static mount now points at `frontend/out` and never the source tree; `phone`/`name`/`job_id` indexes added to `leads`; `review_scraper` captures `last_review_date` before the lowest-first sort.

Tests: 450 passed, 1 skipped. New coverage in `tests/test_latency_fixes.py` (13 cases).

**Deliberately not done:**
- **L11, one browser across tiles.** Requires threading a browser handle through
  the generic multi-source `gather_leads()`. After #3 it's worth ~10 s of
  wall-clock per job. Not worth the churn.
- **Enabling `ENABLE_REVIEW_SCRAPE` / `ENABLE_REGISTRATION_LOOKUP`.** Product
  calls, not bugs — each adds a Maps visit or a network round-trip per delivered
  lead. See LLM-3.
- **The LLM fallback extractor** (§3, last subsection). An enhancement, not a
  defect. ~60 lines, and now is the right time for it.

---

## 3. The LLM path — what it actually does

### Where it runs

`pipeline.py:205-211`, gated on `ENABLE_PITCH_RESEARCH` (currently `true`). It runs **at the very end**, after scoring, after the accuracy gate, after `export_leads` has been chosen — on the delivered slice only.

```
scrape → maps → website → intel → contacts → score → gate
                                                       └→ reviews (off)
                                                       └→ registration (off)
                                                       └→ LLM  ← here
                                                       └→ xlsx + pdf
```

### What it does

`pitch_research.research_leads_sync` (`:306`):

1. `_resolve_backend` (`:286`) — `RESEARCH_PROVIDER=nvidia` + a key present → NVIDIA NIM, 6 threads. Otherwise local Ollama, **serial** (`workers=1`).
2. Per lead, `_lead_facts` (`:250`) reads ~22 **already-scraped ORM fields**: name, category, address, website, CMS, SSL, services, description, rating, review count, price level, external platforms, marketing stack, paid-ads flag, review weaknesses, review snippets, owner name/title, employee count, year founded, socials.
3. `_PROMPT.format(...)` (`:136-165`) drops those into a fixed template, prefixed by a vertical profile from `vertical_profiles.match_profile`.
4. **One** chat completion — `temperature 0.3`, `max_tokens 700`, `response_format: json_object`, `timeout 90` (`:75-89`).
5. `_loads_lenient` (`:111`) parses JSON out of possible prose/fences.
6. Writes back four fields: `pitch_angle`, `pain_points`, `opener`, `review_themes`.

Guardrails that are already right: a 36 rpm token-bucket (`_RateLimiter`, `:25`) under the 40 rpm free tier; a model fallback chain; `_DEAD_MODELS` (`:49`) memoizes a model that 4xxs so only the first lead pays to discover it; per-lead soft-fail so one bad response never aborts the batch.

### The thing to be clear about

**The LLM does not gather any details.** It is pure synthesis over fields the scrapers already produced. It never fetches a URL, never sees raw HTML, never fills a missing field. The file header says so explicitly (`pitch_research.py:5-7`).

What actually "gets the details" is `business_intel.py` — 986 lines of BeautifulSoup + regex extracting services, team members, owner title, employee count, year founded, partners, marketing stack, platform dependencies. Zero LLM.

The consequence: a lead with no live website reaches the LLM with `services=(none listed)`, `description=(none)`, `owner_name=(unknown)`, `employee_count=(unknown)`. The prompt instructs it to treat absence as a signal (`:138-140`), so it dutifully writes "no website → needs web presence" — for every such lead. Across a 200-lead export where roughly half have no crawlable site, that's ~100 near-identical pitches. The model is doing its job; the input is empty.

### Three real problems in the LLM stage

**LLM-1 — `.env` model chain leads with the fragile model.**
```
NVIDIA_MODELS=nvidia/llama-3.3-nemotron-super-49b-v1,meta/llama-3.3-70b-instruct,qwen/qwen2.5-7b-instruct
```
`config.py:92-96` explicitly warns that NVIDIA fine-tunes like nemotron rotate off the free tier more often than first-party instruct models, and puts `meta/llama-3.3-70b-instruct` first for that reason. The `.env` reverses it. `_DEAD_MODELS` limits the damage to the first lead, but that lead can eat a 90 s timeout before falling through.
Fix: match `config.py`'s order in `.env`, or delete the line and let the default win.

**LLM-2 — it runs last, serially, blocking the export.**
Nothing else executes during the LLM stage. At 6-wide with a 70B model (~8–15 s/call), a 200-lead export is `200 / 6 × 12 s ≈ 7 minutes` of dead time before XLSX/PDF generation starts.
Fix: it depends only on already-committed lead fields. Kick it off in a thread as soon as `export_leads` is chosen and join right before `_stage_export` — the exporters are the only consumers. Or better, per L9's logic: start it per-batch on leads that already clear the verify bar, so most of it is done by the time the pipeline reaches the gate.

**LLM-3 — the richest inputs are switched off.**
`ENABLE_REVIEW_SCRAPE=False` and `ENABLE_REGISTRATION_LOOKUP=False` (`config.py:76`, `:80`). So `review_lowest_texts` is always empty, `_review_snippets` (`:235`) always returns `""`, and the `review_themes` field the prompt asks for is structurally always `[]`. Same for legal-entity data. The prompt has slots for evidence that the pipeline is configured never to collect.

Given the header's own framing — *"upgrade path is richer scraped input, not a bigger model"* — this is the correct lever, and it's currently off.

### If you want the LLM to actually gather details

Today the model is a writer. To make it a researcher, the smallest useful step is a **fallback extractor**, not a new stage:

When `business_intel` finishes a lead and `services`/`owner_title`/`description_long` are still empty *but* the homepage HTML was fetched successfully, pass the page's visible text (truncated to ~4 k chars) to the same NVIDIA call with a second prompt: *"extract services, owner name, owner title, team size, year founded from this page text; return null for anything not literally present."*

Why this is the right shape:
- Reuses the existing client, limiter, model chain, and dead-model memoization — no new infrastructure.
- Fires only on the leads where regex already failed, so cost scales with the gap, not the pool.
- Runs on text you already paid to fetch — zero extra network.
- Feeds the *existing* pitch prompt, so pitch quality improves without touching it.

The failure mode to guard is hallucinated fields. That's what `verification_tier` and `data_confidence` are for — an LLM-extracted field should carry its own provenance marker (`employee_count_source` already models this pattern at `business_intel.py:816`) and must not raise `data_confidence`, only fill display fields.

Roughly 60 lines. Do it after items 1–6 above, so it lands on a pipeline that isn't already an hour long.

---

## 4. Non-latency issues found in passing

- **`config.py:38-39` vs `.env`** — `WEBSITE_CONCURRENCY` and `CONTACT_CONCURRENCY` are 12 in `.env` vs 8/10 in code. Harmless, but three settings now differ silently between the two; `.env` is the one that runs.
- **`pipeline.py:266-273`** — `_load_delivered_exclude` scans every `Lead` row across all jobs with no index on `phone`/`name`. Fine at ~10 k rows, quadratic in job count long term.
- **`main.py:58-60`** — mounts `frontend/` (the Next.js *source* directory) as static files at `/`. That serves `package.json`, `tsconfig.json`, and `src/` publicly. Should point at `frontend/.next` or the built export, or be dropped since the README says Next dev-serves on :3000.
- **`main.py:38-44`** — `allow_origins=["*"]` with `allow_credentials=True`. Browsers reject that combination outright; the credentialed path is silently non-functional.
- **`review_scraper.py:167-168`** — `last_review_date` is taken from `reviews[0]` *after* sorting lowest-first, so it's the date of the worst review, not the most recent. The code comment acknowledges it. Since the stage is off, it's latent.
- **`pipeline.py:669-678`** — `_stage_score` assigns `cms_score`/`ssl_score` local variables that are never read. Dead lines.

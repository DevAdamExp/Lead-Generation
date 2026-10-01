# Lifecycle Audit — what is still broken

Date: 2026-07-26. Scope: job creation → queue → scrape → enrich → score → gate →
LLM → export → download → delete. Every finding below is **reproduced against
the live system**, not inferred from reading.

The earlier `latency-and-llm.md` covered latency. This one covers **correctness and
survivability**: what breaks the lifecycle, loses work, or lies to the user.

---

## P0 — permanent stalls and silent data loss

### P0-1 · Jobs stall forever when the worker dies. **Reproduced now.**

`celery_app.py` sets `task_acks_late=False`, so a task is acknowledged the moment
it is delivered. If the worker is killed mid-run (OOM, restart, laptop sleep,
`Ctrl-C`), the task is gone from the broker and the `Job` row keeps whatever
status it had. Nothing anywhere reconciles it.

Live state of `leads.db` right now:

```
RUNNING  16d old  pct=0  stage=Starting   Construction / Miami, FL
RUNNING  16d old  pct=0  stage=Starting   Construction / Los Angeles, CA
PENDING  16d old  pct=0  stage=Queued     Construction / Newark, NJ
PENDING  16d old  pct=0  stage=Queued     Construction / Philadelphia, PA
PENDING  16d old  pct=0  stage=Queued     Construction / Houston, TX
PENDING  16d old  pct=0  stage=Queued     Construction / Seattle, WA
```

Six jobs frozen for 16 days. `usePipeline` polls `/jobs/{id}` every 2 s forever
and shows a spinner; the user has no way to tell a dead job from a slow one.

**Fix:** `task_acks_late=True` (with the existing `task_reject_on_worker_lost`)
so an interrupted run is redelivered; add a `Job.heartbeat_at` touched by `_push`;
reconcile on worker startup — any `RUNNING` job whose heartbeat is older than the
hard time limit becomes `FAILED` with a real message.

### P0-2 · A 2-hour run can deliver **nothing**

`task_soft_time_limit=7200` (120 min). Real runtimes from the pre-rebuild
manifests:

| job | minutes |
|---|---|
| Houston TX | **147.1** |
| Seattle WA | **154.3** |
| Atlanta GA | 78.3 |
| Newark NJ | 65.5 |
| Honolulu HI | 65.2 |

**22% of real runs exceed the limit.** `SoftTimeLimitExceeded` subclasses
`Exception` (verified), so `run_pipeline`'s generic `except Exception` catches it,
marks the job `FAILED`, and re-raises — *before* `_stage_export` ever runs. Two
hours of scraping, enrichment, scoring and LLM work is discarded with no file
written.

**Fix:** catch `SoftTimeLimitExceeded` **before** the generic handler and export
whatever already cleared the gate — a partial delivery beats a total loss. Then
raise the limit; the `latency-and-llm.md` latency fixes should pull runtimes well under it,
but the failure mode must not be catastrophic regardless.

### P0-3 · Job creation reports success even when the queue is unreachable

```python
db.add(job); await db.commit()
background_tasks.add_task(_enqueue_pipeline, job.id)   # ← fires AFTER the response
return job                                             # 202 Accepted
```

`_enqueue_pipeline` calls `run_pipeline.delay()`. If Redis or Celery is down it
raises **inside a background task**, long after the client received `202` and a
`PENDING` job row. The job sits at "Queued" forever. This is exactly the four
`PENDING` rows above.

**Fix:** enqueue synchronously inside the request handler; on failure mark the job
`FAILED` with the broker error and return `503`. The user learns immediately.

### P0-4 · Deleting a job orphans its exports *and* re-opens delivered leads

`DELETE /jobs/{job_id}` removes the `Job` row; `cascade="all, delete-orphan"`
removes its `Lead` rows. It does **not** touch `exports/<dir>/`.

Two consequences:

1. **Orphaned files.** This is precisely what produced the Atlanta / Honolulu /
   Manchester folders — 175 delivered leads whose DB rows were gone, leaving the
   XLSX as the only surviving copy.
2. **Broken cross-run dedup.** `_load_delivered_exclude` builds its exclusion set
   by querying `Lead` rows. Delete a job and every business it delivered becomes
   re-deliverable — you can sell the same lead to the same client twice.

**Fix:** delete the export directory alongside the job, and record deliveries in a
small `delivered_business(phone7, name_norm, delivered_at)` table that is **not**
cascaded, so dedup survives job deletion. That table is also what makes dedup
cheap at scale.

---

## P1 — silently wrong behaviour

### P1-1 · The API never returns the LLM output

`LeadResponse` exposes 54 of the `Lead` model's 77 columns. Missing (23):

```
pitch_angle, pain_points, opener, review_themes,      ← the entire LLM output
marketing_stack, external_platforms, has_direct_commerce, runs_paid_ads,
legal_name, entity_type, entity_status, registration_date, registered_agent,
registry_source, last_review_date, rating_histogram, owner_responds,
review_lowest_texts, phone_source_count, fuzzy_confidence, google_is_open,
source_query, created_at
```

Every pitch angle, pain point and cold-email opener the pipeline generates is
written to SQLite and rendered into XLSX/PDF — and is **invisible to the app**.
The UI cannot show it, filter on it, or let a user copy an opener.

### P1-2 · `/health` always reports `degraded`

```python
all_ok = all(v == "ok" or (isinstance(v, str) and "/" in v) for v in status.values())
```

`status["proxy_details"]` is a **dict**, so the generator yields `False` on every
call and `all_ok` can never be true. Verified live: every service reported `ok`
and the endpoint still returned `degraded`.

Worse, `"0/0 healthy"` — meaning *zero working proxies* — passes the `"/" in v`
test. The check both under-reports health and cannot detect a total proxy outage.

### P1-3 · WebSocket and frontend poll forever

`ws.py` runs `while True:` reading Redis every 0.5 s with no maximum duration and
no idle timeout. `usePipeline.startPolling` runs a 2 s `setInterval` that only
clears on `completed` / `failed`. Against the six stuck jobs above, both spin
indefinitely — one server connection and one browser timer per abandoned tab.

**Fix:** cap the WS loop (e.g. exit after the hard time limit, or after N cycles
with no field change) and send a terminal `error` frame so the client stops.

---

## P2 — hygiene and resource drift

| # | Finding |
|---|---|
| P2-1 | **No export retention.** `exports/` grows unbounded; nothing ever deletes. 4.6 MB today, but it is monotonic. |
| P2-2 | `create_tables_sync()` → `migrate_sqlite_sync()` runs on **every task**: full `inspect()`, an `ALTER TABLE` attempt per missing column, plus the new `CREATE INDEX` statements — per job, forever. Should run once at deploy/startup. |
| P2-3 | **No resume.** A failure at 90% re-runs the whole pipeline; `max_retries=1` retries the entire task. The scrape stage is idempotent (it deletes the job's leads first) but enrichment is not. |
| P2-4 | `deduplicate_raw` in `utils/deduplicator.py` is **dead code** — imported only by 18 test cases, never by the pipeline. `sources.merge_sources` does the real dedup. |
| P2-5 | `worker_concurrency=1` means a second job waits behind a multi-hour first job while showing only `PENDING` — no queue position, no ETA. |
| P2-6 | My export rebuild overwrote `completed_at` in six manifests with today's date, so their recorded durations are now nonsense (21015 min). Original values survive in `scratchpad/exports-before/`. Cosmetic, but it is wrong data I introduced and should be restored. |

---

## Using the NVIDIA key for per-lead LLM research

Today the LLM is a **writer**, not a researcher: `pitch_research` synthesises over
fields the scrapers already produced, and `business_intel` does all extraction
with regex + BeautifulSoup. When regex fails — no `services`, no `owner_title`,
no `description` — the prompt receives `(none)` and the model writes a generic
"no website → needs web presence" pitch. That is the single biggest quality
ceiling left.

The NVIDIA key removes the reason this was deferred (a 0.5B local model could not
be trusted to extract without hallucinating; `meta/llama-3.1-8b-instruct` answers
in 0.8 s and handled 187 leads in 6.7 min).

**Proposed: an extraction fallback, not a new stage.**

- Trigger only where regex already failed *and* `business_intel` successfully
  fetched the homepage — so cost scales with the gap, not the pool.
- Feed the page's visible text (~4k chars, already in the shared page cache from
  `latency-and-llm.md` fix #6 — **zero extra network**) with a strict prompt: extract
  `services`, `owner_name`, `owner_title`, `employee_count`, `year_founded`;
  return `null` for anything not literally present.
- Reuse the existing client, `_RateLimiter`, model chain, `_DEAD_MODELS` and the
  new strike logic. No new infrastructure.
- **Provenance is mandatory.** Follow the `employee_count_source` pattern already
  in the model: every LLM-extracted value carries `*_source="llm"`, and must
  **not** raise `data_confidence` — that score is reserved for independently
  verified signals. An extracted `owner_name` must still pass
  `is_plausible_person_name`.

That last constraint is the whole ballgame. The pipeline's value is its
verification posture; an LLM that can quietly invent an owner name would poison
`lead_score`, `data_confidence` and the export gate at once.

**Second, cheaper win:** run the extraction pass *before* `_stage_score`, so
recovered `services`/`owner_title`/`employee_count` actually earn their scoring
points. Run it after, and the fields fill but the score never reflects them.

---

## Status — all P0/P1 and most of P2 shipped 2026-07-26

| # | Fix | Status |
|---|---|---|
| P0-1 | `acks_late=True`, `Job.heartbeat_at`, `reconcile_stale_jobs()` on API **and** worker startup | ✅ ran it — the six 16-day-old jobs are now `FAILED` with an honest message |
| P0-2 | `SoftTimeLimitExceeded` caught before the generic handler; exports what cleared the gate | ✅ partial delivery instead of total loss |
| P0-3 | Enqueue moved out of `BackgroundTasks` into the request; `503` + `FAILED` if the broker is down | ✅ |
| P0-4 | `delivered_businesses` table (no FK, never cascaded); `DELETE /jobs` also removes the export dir | ✅ backfilled 245 records (70 live + 175 reconstructed from the orphaned exports) |
| P1-1 | `LeadResponse` now exposes **all 77** Lead columns (was 54) | ✅ test asserts full coverage so a new column can't be hidden again |
| P1-2 | `/health` verdict computed over service keys only; `0/5 healthy` is degraded, `0/0` is ok | ✅ |
| P1-3 | WS loop and frontend poll both bounded just past the Celery hard limit | ✅ |
| — | NVIDIA extraction fallback, before `_stage_score` | ✅ live: recovered `Michael Ortega` + founding year from a page regex missed; returned `{}` for a JS-only page |
| P2-2 | `create_tables_sync()` memoised per process | ✅ was a full inspect + ALTER sweep per job |
| P2-3 | Resume on retry — reuse the scraped pool, skip already-`VALIDATED` leads | ✅ closes the loop `acks_late` opened |
| P2-4 | `utils/deduplicator.py` deleted with its 18 tests | ✅ dead code |
| P2-6 | Restored the real `completed_at`/counts in six manifests | ✅ my own damage, fixed |

**Deliberately not done:**

- **P2-1 auto export retention.** An orphan sweep would have deleted the Atlanta /
  Honolulu / Manchester folders — 175 real delivered leads whose DB rows were
  already gone. Auto-deleting client deliverables to reclaim 4.6 MB is a bad
  trade. Now that `DELETE /jobs` removes its own folder, new orphans shouldn't
  accumulate. Revisit only if disk actually becomes a constraint, and even then
  make it opt-in with a dry-run.
- **P2-5 queue-position feedback.** Cosmetic, and it would put a Celery
  queue-inspection call on the API hot path.

**Watch on the first real run:** `acks_late=True` means an interrupted job is
retried. Resume makes that cheap, but combined with `max_retries=1` a genuinely
poisonous job now runs twice before failing.

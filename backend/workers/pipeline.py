"""
workers/pipeline.py — Master Celery task orchestrating all 6 pipeline stages.

Fixes applied:
  #1  Lazy Redis init — no module-level connection attempt
  #2  Scrape pagination uses page param correctly
  #5  Google Maps uses BATCH scraper (one browser for all leads)
  #7  sys.path dedup guard
  #10 Ordered lead refresh query
"""
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import redis
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from backend.workers.celery_app import celery_app
from backend.config import settings
from backend.database import create_tables_sync, SyncSessionLocal
from backend.models import (Job, Lead, DeliveredBusiness, JobStatus, LeadStatus,
                            WebsiteStatus, LeadCategory)

logger = logging.getLogger(__name__)

# Bug #7 fix — add hotfrog path once at module level with dedup guard
_hotfrog_path = str(Path(__file__).parent.parent.parent / "hotfrog")
if _hotfrog_path not in sys.path:
    sys.path.insert(0, _hotfrog_path)

# Bug #1 fix — lazy Redis init
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _push(job_id: str, pct: int, stage: str, msg: str,
          type_: str = "progress", scraped: int = 0, verified: int = 0):
    try:
        r = _get_redis()
        r.hset(f"job:{job_id}:progress", mapping={
            "type": type_,
            "progress_pct": pct,
            "stage": stage,
            "message": msg,
            "total_scraped": scraped,
            "total_verified": verified,
        })
        r.expire(f"job:{job_id}:progress", 3600)
    except Exception as e:
        logger.warning("Redis push failed (non-fatal): %s", e)


def _update_job(db, job: Job, **kwargs):
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.heartbeat_at = datetime.now(timezone.utc)   # liveness for reconcile_stale_jobs
    db.commit()


def _touch(db, job: Job):
    """Heartbeat only — cheap enough to call from inside a long stage."""
    try:
        job.heartbeat_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as e:                          # never fail a job over a heartbeat
        logger.debug("heartbeat failed: %s", e)


# How long a RUNNING job may go without a heartbeat before we call it dead. Must
# exceed the Celery hard time limit so we never fail a job that is merely slow.
STALE_AFTER_SECONDS = 9000   # 150 min vs the 130 min hard limit


def reconcile_stale_jobs() -> int:
    """Fail jobs that can never finish. Called at API startup and worker startup.

    Two unrecoverable shapes:
      - RUNNING with a heartbeat older than STALE_AFTER_SECONDS (worker died
        mid-run, or died before acks_late existed and left no heartbeat at all).
      - PENDING that was never picked up — with the enqueue now synchronous this
        can only be a pre-existing row or a worker that never came up.

    Returns how many jobs were reconciled.
    """
    from datetime import timedelta

    db = SyncSessionLocal()
    n = 0
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=STALE_AFTER_SECONDS)
        stuck = (db.query(Job)
                   .filter(Job.status.in_([JobStatus.RUNNING, JobStatus.PENDING]))
                   .all())
        for job in stuck:
            marker = job.heartbeat_at or job.created_at
            if marker is not None and marker.tzinfo is None:
                marker = marker.replace(tzinfo=timezone.utc)
            if marker is not None and marker > cutoff:
                continue                     # still plausibly alive — leave it
            job.status = JobStatus.FAILED
            job.current_stage = "Failed"
            job.stage_message = (
                "Worker stopped before this job finished (no heartbeat) — "
                "re-run the search."
            )
            job.error_message = (
                f"reconciled at startup: last activity {marker.isoformat() if marker else 'never'}"
            )[:1000]
            job.completed_at = now
            n += 1
        if n:
            db.commit()
            logger.warning("Reconciled %d stale job(s) left RUNNING/PENDING by a dead worker", n)
    except Exception as e:
        logger.warning("Job reconciliation failed (non-fatal): %s", e)
    finally:
        db.close()
    return n


def _is_verified(lead) -> bool:
    """A lead counts as VERIFIED (a usable B2B lead) if EITHER:
      - it scores >= VERIFY_MIN_SCORE (the rich, email/website-backed path), OR
      - it has a valid phone AND a Google Maps presence (a real, callable
        business), OR
      - it's NPI-registered (official US healthcare registry) with a valid phone.
    Email/website are a quality bonus, not a hard gate — so phone-reachable
    businesses without a crawlable email still count."""
    if (lead.lead_score or 0) >= settings.VERIFY_MIN_SCORE:
        return True
    if lead.phone_verified and lead.has_google_maps:
        return True
    if lead.phone_verified and "npi" in (lead.sources or ""):
        return True
    return False


def _is_contactable(lead) -> bool:
    """A lead is reachable if it has at least one of phone / owner email / website."""
    return bool(lead.phone or lead.owner_email or lead.website)


def _is_accurate(lead) -> bool:
    """Stricter than _is_verified — the FINAL export gate that targets >95%-real
    delivered data. A lead qualifies only if it clears the confidence floor AND
    has either an independently-verified contact channel (phone or email passed
    the MX/SMTP / libphonenumber checks) OR strong cross-corroboration (on Google
    Maps AND confirmed by >= 2 sources). Applied with an empty-set fallback in
    the caller so a job is never emptied out by the gate."""
    if (lead.data_confidence or 0) < settings.MIN_EXPORT_CONFIDENCE:
        return False
    if lead.phone_verified or lead.email_verified:
        return True
    n_sources = len((lead.sources or "").split(",")) if lead.sources else 0
    return bool(lead.has_google_maps and n_sources >= 2)


def select_export_leads(leads: list, target: int) -> list:
    """Choose what the client actually receives, best-first.

    Extracted from run_pipeline so the rules are testable and so exports can be
    rebuilt from stored leads without re-running a job. Cascade of gates, each
    with a never-deliver-nothing fallback:

      1. verified pool      — _is_verified (score, or phone+maps, or npi+phone)
      2. accuracy gate      — _is_accurate (confidence floor + a verified channel
                              or multi-source corroboration); falls back to the
                              full verified pool if too strict to fill the order
      3. cap at `target`, else any VALIDATED lead, else everything
      4. contactable only   — has phone / email / website; kept if that empties
    """
    def rank(l):
        return (l.lead_score or 0, l.data_confidence or 0)

    verified_pool = [l for l in leads if _is_verified(l)]
    accurate_pool = [l for l in verified_pool if _is_accurate(l)]
    verified_leads = sorted(accurate_pool or verified_pool, key=rank, reverse=True)[:target]
    export_leads = (verified_leads
                    or [l for l in leads if l.status == LeadStatus.VALIDATED]
                    or leads)
    # ponytail: drop leads with no contact path; never deliver an empty file.
    contactable = [l for l in export_leads if _is_contactable(l)]
    return sorted(contactable or export_leads, key=rank, reverse=True)


def _build_manifest(job, leads: list, total_scraped: int, total_verified: int,
                    filenames: dict) -> dict:
    """Pure builder for the per-export manifest dict (testable without files)."""
    scores = [l.lead_score or 0 for l in leads]
    return {
        "job_id": getattr(job, "id", None),
        "niche": job.niche,
        "location": job.location,
        "country": getattr(job, "country", None),
        "limit": getattr(job, "limit", None),
        "created_at": (getattr(job, "created_at", None) or datetime.now(timezone.utc)).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "total_scraped": total_scraped,
            "total_verified": total_verified,
            "exported": len(leads),
        },
        "lead_score": {
            "min": min(scores) if scores else 0,
            "max": max(scores) if scores else 0,
            "avg": round(sum(scores) / len(scores), 1) if scores else 0,
        },
        "files": filenames,
    }


# ── Main pipeline task ────────────────────────────────────────────────────────
@celery_app.task(bind=True, name="pipeline.run", max_retries=1)
def run_pipeline(self: Task, job_id: str):
    create_tables_sync()
    db = SyncSessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            return

        _update_job(db, job, status=JobStatus.RUNNING,
                    current_stage="Starting", stage_message="Pipeline initialising…")
        _push(job_id, 2, "Starting", "Pipeline starting…")

        # `job.limit` is the VERIFIED-lead target the user asked for.
        target = max(1, job.limit)
        pool_target = min(int(target * settings.POOL_FACTOR), settings.POOL_MAX)

        # Stage 1 — Scrape an over-provisioned candidate pool (tiled across query
        # variants so we can exceed a single query's ~48-result ceiling).
        #
        # RESUME: acks_late means a killed worker's task is redelivered, which
        # used to redo the whole run from zero — up to two hours of rework. The
        # scrape commits delete+insert+commit as ONE transaction, so the presence
        # of leads proves the scrape ran to completion; on a retry we reuse that
        # pool instead of re-scraping it.
        existing = db.query(Lead).filter(Lead.job_id == job.id).count()
        resuming = existing > 0
        if resuming:
            logger.info("Resuming job %s — reusing %d already-scraped candidates",
                        job_id, existing)
            _push(job_id, 20, "Scraping",
                  f"Resuming — reusing {existing} candidates already scraped")
            leads = (db.query(Lead).filter(Lead.job_id == job.id)
                       .order_by(Lead.created_at).all())
        else:
            _push(job_id, 5, "Scraping",
                  f"Scraping up to {pool_target} candidates for '{job.niche}' in '{job.location}'…")
            leads = _stage_scrape(db, job, pool_target)
        _update_job(db, job, total_scraped=len(leads), current_stage="Scraping",
                    stage_message=f"Scraped {len(leads)} candidate businesses")
        _push(job_id, 22, "Scraping", f"Scraped {len(leads)} candidates", scraped=len(leads))

        if not leads:
            _update_job(db, job, status=JobStatus.COMPLETED,
                        current_stage="Done", stage_message="No businesses found.",
                        completed_at=datetime.now(timezone.utc))
            _push(job_id, 100, "Done", "No businesses found.", type_="complete")
            return

        # Stages 2-5 — enrich candidates best-first, in batches, until we have
        # `target` VERIFIED leads (or the candidate pool is exhausted).
        verified = _enrich_until_target(db, job, leads, target)

        short = verified < target
        _update_job(db, job, total_verified=verified, current_stage="Scoring",
                    stage_message=(f"{verified} leads verified"
                                   + (" (candidate pool exhausted before target)" if short else "")),
                    progress_pct=90)
        _push(job_id, 90, "Scoring", f"{verified}/{target} leads verified.",
              scraped=len(leads), verified=verified)

        # Stage 6 — Export the best `target` verified leads.
        export_leads = select_export_leads(leads, target)

        # Deep-depth stages run ONLY on the export slice (cost control) — real
        # review content + legal registration. All soft-fail.
        # Reviews must land BEFORE pitch research: review_lowest_texts feeds the
        # LLM prompt (pitch_research._review_snippets).
        if getattr(settings, "ENABLE_REVIEW_SCRAPE", False):
            _stage_reviews(db, job, export_leads)

        # Registration (network I/O) and pitch research (LLM) write disjoint field
        # sets and neither reads the other's output — overlap them instead of
        # queueing. Both mutate lead objects on worker threads; the single commit
        # happens here on the main thread, because a Session is not thread-safe.
        #
        # ponytail: with ENABLE_REGISTRATION_LOOKUP off this saves nothing — the
        # LLM tail is then bound by the NVIDIA free-tier 36 rpm limiter at roughly
        # len(export_leads)/36 minutes, which no amount of concurrency fixes.
        # Levers there are a paid tier or researching fewer than the full slice.
        from concurrent.futures import ThreadPoolExecutor as _TailPool
        tail = []
        with _TailPool(max_workers=2) as ex:
            if getattr(settings, "ENABLE_REGISTRATION_LOOKUP", False):
                tail.append(ex.submit(_stage_registration, job, export_leads))
            if getattr(settings, "ENABLE_PITCH_RESEARCH", False):
                tail.append(ex.submit(_stage_pitch_research, export_leads))
            for fut in tail:
                try:
                    fut.result()
                except Exception as e:
                    logger.warning("tail stage failed (non-fatal): %s", e)
        if tail:
            db.commit()

        _update_job(db, job, current_stage="Exporting",
                    stage_message="Generating XLSX & PDF exports…", progress_pct=92)
        _push(job_id, 92, "Exporting", "Generating XLSX & PDF exports…",
              scraped=len(leads), verified=verified)
        xlsx_path, pdf_path = _stage_export(db, job, export_leads,
                                            total_scraped=len(leads), total_verified=verified)
        # Only AFTER the files exist — a business isn't "delivered" until the user
        # can actually download it.
        _record_delivered(db, job, export_leads)

        _update_job(
            db, job,
            status=JobStatus.COMPLETED,
            current_stage="Done",
            stage_message=f"Pipeline complete — {len(export_leads)} verified leads.",
            export_xlsx_path=str(xlsx_path) if xlsx_path else None,
            export_pdf_path=str(pdf_path) if pdf_path else None,
            completed_at=datetime.now(timezone.utc),
            progress_pct=100
        )
        _push(job_id, 100, "Done",
              f"Complete! {len(export_leads)} verified leads ready for download.",
              type_="complete", scraped=len(leads), verified=verified)

    except SoftTimeLimitExceeded:
        # The 120-min soft limit fired. This used to fall into the generic handler
        # below: job marked FAILED, _stage_export never reached, and TWO HOURS of
        # scraping + enrichment + LLM work thrown away with no file written
        # (2 of 9 observed real runs exceeded the limit). Salvage instead — we
        # have until the hard limit to write what already cleared the gate.
        logger.warning("Soft time limit hit for job %s — exporting partial results", job_id)
        try:
            job = db.get(Job, job_id)
            done = db.query(Lead).filter(Lead.job_id == job_id,
                                         Lead.status == LeadStatus.VALIDATED).all()
            partial = select_export_leads(done, max(1, job.limit)) if (job and done) else []
            xlsx_path = pdf_path = None
            if partial:
                xlsx_path, pdf_path = _stage_export(
                    db, job, partial, total_scraped=job.total_scraped or 0,
                    total_verified=len(partial))
                _record_delivered(db, job, partial)
            _update_job(
                db, job,
                status=JobStatus.COMPLETED if partial else JobStatus.FAILED,
                current_stage="Done" if partial else "Failed",
                stage_message=(f"Timed out — partial delivery of {len(partial)} leads."
                               if partial else "Timed out before any lead was ready."),
                error_message="Soft time limit exceeded; results are partial.",
                export_xlsx_path=str(xlsx_path) if xlsx_path else None,
                export_pdf_path=str(pdf_path) if pdf_path else None,
                completed_at=datetime.now(timezone.utc), progress_pct=100)
            _push(job_id, 100, "Done",
                  f"Timed out — {len(partial)} leads salvaged and exported.",
                  type_="complete" if partial else "error")
        except Exception as e:
            logger.exception("Partial export after timeout failed: %s", e)
        raise

    except Exception as exc:
        logger.exception("Pipeline failed for job %s", job_id)
        try:
            job = db.get(Job, job_id)
            if job:
                _update_job(db, job, status=JobStatus.FAILED,
                            error_message=str(exc)[:1000],
                            current_stage="Failed",
                            stage_message=str(exc)[:500])
        except Exception:
            pass
        _push(job_id, 0, "Error", str(exc)[:500], type_="error")
        raise
    finally:
        db.close()


# ── Stage implementations ─────────────────────────────────────────────────────

def _record_delivered(db, job: Job, leads: list) -> int:
    """Persist "we already handed the user this business" so it survives the job.

    Cross-run dedup used to be derived purely from `leads`, which cascade-deletes
    with their Job — so deleting a job made everything it delivered sellable
    again. Writing here makes the fact permanent. Idempotent per (phone7,
    name_norm) so a Celery retry never double-inserts. Soft-fails: never lose a
    finished export over a bookkeeping row.
    """
    from backend.services.sources import _normalize_name, _digits

    try:
        existing = {(p, n) for p, n in db.query(DeliveredBusiness.phone7,
                                                DeliveredBusiness.name_norm).all()}
        added = 0
        for l in leads:
            d = _digits(l.phone)
            phone7 = d[-7:] if len(d) >= 7 else None
            name_norm = _normalize_name(l.name or "") or None
            if not phone7 and not name_norm:
                continue
            if (phone7, name_norm) in existing:
                continue
            existing.add((phone7, name_norm))
            db.add(DeliveredBusiness(
                phone7=phone7, name_norm=name_norm, business_name=l.name,
                job_id=getattr(job, "id", None), niche=getattr(job, "niche", None),
                location=getattr(job, "location", None)))
            added += 1
        if added:
            db.commit()
            logger.info("Recorded %d newly delivered businesses", added)
        return added
    except Exception as e:
        logger.warning("Delivered-business bookkeeping failed (non-fatal): %s", e)
        db.rollback()
        return 0


def _load_delivered_exclude(db, niche: str, current_job_id: str):
    """Build a predicate that excludes businesses ALREADY DELIVERED (verified) in
    ANY prior job — so we never hand the same business to the user twice.

    Global (niche-independent): a business delivered for one campaign won't be
    re-delivered for another. Matches on phone (last 7 digits) or normalised
    name, reusing the same keys the in-run de-duper uses. `niche` is accepted for
    logging/compat but no longer scopes the query.
    """
    from sqlalchemy import or_, and_
    from backend.services.sources import _normalize_name, _digits

    # "Delivered" must mirror _is_verified(): rich score OR phone+maps OR npi+phone.
    rows = (db.query(Lead.name, Lead.phone)
              .filter(Lead.job_id != current_job_id,
                      or_(
                          Lead.lead_score >= settings.VERIFY_MIN_SCORE,
                          and_(Lead.phone_verified.is_(True), Lead.has_google_maps.is_(True)),
                          and_(Lead.phone_verified.is_(True), Lead.sources.like("%npi%")),
                      ))
              .all())

    phones, names = set(), set()
    for name, phone in rows:
        d = _digits(phone)
        if len(d) >= 7:
            phones.add(d[-7:])
        nn = _normalize_name(name or "")
        if nn:
            names.add(nn)

    # The durable record — survives job deletion, unlike the `leads` scan above
    # (which stays for rows delivered before this table existed).
    try:
        n_durable = 0
        for phone7, name_norm in db.query(DeliveredBusiness.phone7,
                                          DeliveredBusiness.name_norm).all():
            if phone7:
                phones.add(phone7)
            if name_norm:
                names.add(name_norm)
            n_durable += 1
        logger.info("Delivered-business table contributed %d records to the exclude set",
                    n_durable)
    except Exception as e:
        logger.warning("Could not read delivered_businesses: %s", e)

    def exclude(rec: dict) -> bool:
        d = _digits(rec.get("phone"))
        if len(d) >= 7 and d[-7:] in phones:
            return True
        nn = _normalize_name(rec.get("name") or "")
        return bool(nn and nn in names)

    return exclude, len(rows)


def _stage_scrape(db, job: Job, pool_target: int) -> list:
    """
    Multi-source, TILED scrape: runs several query variants across Google Maps
    (primary) + YellowPages + Yelp + Hotfrog, merged & de-duplicated into a
    candidate pool of up to `pool_target` unique businesses, ranked best-first
    (most likely to verify). The pool is intentionally larger than the verified
    target so the enrich loop can keep going until enough leads clear scoring.
    """
    from backend.services.sources import gather_leads_tiled

    query = f"{job.niche} {job.location}".strip()

    def on_source_progress(stage: str, msg: str):
        _push(job.id, 12, "Scraping", msg)

    # Cross-run dedup: never re-deliver a business already delivered in this niche.
    exclude, n_prior = _load_delivered_exclude(db, job.niche, job.id)
    if n_prior:
        logger.info("Cross-run dedup: excluding %d previously-delivered leads (global)",
                    n_prior)
        _push(job.id, 6, "Scraping",
              f"Skipping {n_prior} previously-delivered leads…")

    merged = gather_leads_tiled(
        niche=job.niche,
        location=job.location,
        country=job.country,
        candidate_target=pool_target,
        on_progress=on_source_progress,
        exclude=exclude,
    )

    # ponytail: scrape is idempotent per job. On a Celery retry / re-run (acks_late
    # + max_retries) or after a mid-scrape crash left partial rows, wipe this job's
    # leads first so we rebuild instead of double-inserting. Delete + inserts + the
    # final commit are one transaction, so a crash mid-loop commits nothing.
    db.query(Lead).filter(Lead.job_id == job.id).delete(synchronize_session=False)

    for idx, r in enumerate(merged):
        lead = Lead(
            job_id=job.id,
            name=r.get("name"),
            address=r.get("address"),
            phone=r.get("phone"),
            description=r.get("description"),
            website=r.get("website"),
            business_category=r.get("category"),
            hours=r.get("hours"),
            price_level=r.get("price_level"),
            plus_code=r.get("plus_code"),
            latitude=r.get("latitude"),
            longitude=r.get("longitude"),
            sources=r.get("sources_str"),
            phone_source_count=r.get("phone_source_count") or 0,
            source_url=r.get("source_url") or r.get("google_maps_url"),
            hotfrog_url=r.get("hotfrog_url"),
            google_maps_url=r.get("google_maps_url"),
            has_google_maps=bool(r.get("has_google_maps")),
            google_rating=r.get("google_rating"),
            google_review_count=r.get("google_review_count") or 0,
            google_is_open=r.get("google_is_open"),
            social_facebook=r.get("social_facebook"),
            social_instagram=r.get("social_instagram"),
            social_twitter=r.get("social_twitter"),
            social_linkedin=r.get("social_linkedin"),
            source_query=query,
            status=LeadStatus.SCRAPED,
        )
        db.add(lead)

        pct = 12 + int(10 * (idx + 1) / max(len(merged), 1))
        _push(job.id, pct, "Scraping", f"Saved {idx+1}/{len(merged)}: {lead.name}", scraped=idx + 1)

    db.commit()  # single transaction for the whole scrape (idempotent + fewer SQLite locks)
    return (db.query(Lead)
              .filter(Lead.job_id == job.id)
              .order_by(Lead.created_at)
              .all())


def _enrich_until_target(db, job: Job, leads: list, target: int) -> int:
    """Enrich candidates best-first in batches and stop once `target` leads are
    verified (per _is_verified) or the pool is exhausted.

    Each batch runs the full enrich chain (Maps → website → contacts → score) so
    we only pay the per-lead cost for as many candidates as needed to hit the
    target — not the whole over-provisioned pool. Returns the verified count.
    """
    from concurrent.futures import ThreadPoolExecutor

    batch_size = max(1, settings.ENRICH_BATCH_SIZE)
    total = len(leads)
    # RESUME: count work already done in a previous attempt up front, so both the
    # progress numbers and the early-exit check are right even when the retry has
    # nothing left to do.
    processed = sum(1 for l in leads if l.status == LeadStatus.VALIDATED)
    verified = sum(1 for l in leads if _is_verified(l))
    if processed:
        logger.info("Resuming enrichment: %d/%d already scored, %d already verified",
                    processed, total, verified)
    if verified >= target:
        return verified

    for start in range(0, total, batch_size):
        window = leads[start:start + batch_size]
        # _stage_score is the last step per batch, so VALIDATED means the whole
        # enrich chain completed for that lead — skip it on a retry.
        batch = [l for l in window if l.status != LeadStatus.VALIDATED]
        if not batch:
            continue
        base_pct = 25 + int(60 * start / max(total, 1))

        _update_job(db, job, current_stage="Enriching",
                    stage_message=f"Enriching ({verified}/{target} verified)…",
                    progress_pct=base_pct)
        _push(job.id, base_pct, "Enriching",
              f"{verified}/{target} verified — enriching {start + 1}-"
              f"{min(start + batch_size, total)} of {total}…",
              scraped=total, verified=verified)

        # Maps enrichment (browser) and website/intel/contacts (HTTP pool) contend
        # for nothing — Maps needs only name+city, the crawlers need only .website.
        # They used to run as sequential barriers, leaving the 12-thread HTTP pool
        # idle for the whole browser stage. Overlap them.
        #
        # The Maps thread does NETWORK ONLY and returns its results; every ORM
        # write and the commit happen back on this thread. A SQLAlchemy Session is
        # not thread-safe, so two stages must never commit it concurrently.
        maps_args = _stage_maps_plan(job, batch)
        with ThreadPoolExecutor(max_workers=1) as ex:
            maps_fut = ex.submit(_stage_maps_fetch, job, maps_args)
            _stage_websites(db, job, batch)
            # Intel + contacts both crawl the same website — run them back-to-back
            # in ONE thread pool (per-lead) instead of two sequential waves.
            _stage_intel_and_contacts(db, job, batch,
                                      pct_lo=base_pct, pct_hi=min(base_pct + 11, 88))
            maps_results = maps_fut.result()
        _stage_maps_apply(db, batch, maps_args, maps_results)
        _stage_score(db, job, batch)

        processed += len(batch)
        verified = sum(1 for l in leads if _is_verified(l))
        _push(job.id, min(25 + int(60 * processed / max(total, 1)), 88), "Enriching",
              f"{verified}/{target} verified ({processed}/{total} enriched)",
              scraped=total, verified=verified)

        if verified >= target:
            logger.info("Reached verified target %d after enriching %d/%d candidates",
                        target, processed, total)
            break

    return verified


def _stage_maps_plan(job: Job, leads: list) -> list:
    """Read the ORM fields the Maps lookup needs, on the CALLING thread.

    Split out of the old _stage_maps so the browser work can run on a worker
    thread without ever touching the Session (see _enrich_until_target).

    Only enrich leads that don't already have Google Maps data (i.e. those
    discovered via YellowPages/Yelp/Hotfrog). Maps-sourced leads already carry
    rating/reviews/open-status from the primary scrape.

    Skip NPI-sourced leads that have a phone: they already qualify as verified
    (npi + valid phone), so the slow per-lead Maps lookup would only add a
    nice-to-have rating — not worth blocking a batch of hundreds on it.
    """
    def _needs_maps(lead) -> bool:
        if not lead.name or lead.has_google_maps:
            return False
        if lead.phone and "npi" in (lead.sources or ""):
            return False
        return True

    return [
        {
            "id": lead.id,
            "name": lead.name or "",
            "city": (lead.address or job.location).split(",")[0].strip(),
        }
        for lead in leads if _needs_maps(lead)
    ]


def _stage_maps_fetch(job: Job, leads_data: list) -> dict:
    """Network only — ONE browser for all leads, no DB access. Safe off-thread."""
    from backend.services.google_maps_scraper import scrape_maps_ratings_batch_sync

    if not leads_data:
        logger.info("All leads already have Google Maps data — skipping enrichment")
        return {}

    def on_progress(idx, total, name):
        pct = 25 + int(20 * idx / max(total, 1))
        _push(job.id, pct, "Maps", f"Maps {idx}/{total}: {name}")

    try:
        return scrape_maps_ratings_batch_sync(leads_data, on_progress=on_progress)
    except Exception as e:
        logger.error("Batch Maps scrape failed: %s", e)
        return {}


def _stage_maps_apply(db, leads: list, leads_data: list, results: dict):
    """Write Maps results back — main thread only (Session is not thread-safe).

    Applies ONLY to the leads we actually enriched, never blanking existing Maps
    data on Maps-sourced leads (which weren't in leads_data).
    """
    if not leads_data:
        return
    enriched_ids = {d["id"] for d in leads_data}
    for lead in leads:
        if lead.id not in enriched_ids:
            continue
        info = results.get(lead.id, {})
        lead.has_google_maps     = info.get("has_google_maps", False)
        lead.google_rating       = info.get("google_rating")
        lead.google_review_count = info.get("google_review_count", 0)
        lead.google_maps_url     = info.get("google_maps_url")
        lead.google_is_open      = info.get("google_is_open")
        # No status write: _stage_intel_and_contacts already advanced these leads
        # to ENRICHED and _stage_score sets VALIDATED next. MAPS_CHECKED would
        # only move them backwards, and nothing reads it (only VALIDATED is).
    db.commit()


def _stage_websites(db, job: Job, leads: list):
    from backend.services.website_checker import check_all_websites_sync
    check_all_websites_sync(leads, db)
    for lead in leads:
        lead.status = LeadStatus.WEBSITE_ANALYZED
    db.commit()


def _stage_intel_and_contacts(db, job: Job, leads: list, pct_lo: int = 25, pct_hi: int = 82):
    """Business intel + contact enrichment for each lead, back-to-back, in ONE
    thread pool.

    Both steps crawl the lead's own website and are I/O-bound; each only reads
    already-loaded scalar fields and writes attributes on ITS OWN lead, so a
    single shared session is safe as long as we commit once from the main thread
    afterwards. Running them in one pool (was two sequential waves) overlaps a
    slow lead's intel crawl with another lead's contact crawl.
    """
    from backend.services.business_intel import enrich_business_intel_sync
    from backend.services.contact_finder import enrich_lead_sync
    from backend.utils.validator import COUNTRY_TO_REGION
    from concurrent.futures import ThreadPoolExecutor, as_completed

    region = COUNTRY_TO_REGION.get(job.country, "US")
    workers = max(1, min(settings.CONTACT_CONCURRENCY, len(leads) or 1))

    def _enrich(lead):
        # One page cache per lead, shared by both crawlers: they hit ~8 of the
        # same paths (/about, /about-us, /team, /our-team, /staff, /leadership…)
        # on the same host, so every overlap used to be fetched twice.
        cache: dict = {}
        # Intel first (only if there's a site to crawl), then contacts.
        if lead.website:
            try:
                enrich_business_intel_sync(lead, cache=cache)
            except Exception as e:
                logger.warning("Business intel failed for '%s': %s", lead.name, e)
        try:
            enrich_lead_sync(lead, region=region, cache=cache)
            lead.status = LeadStatus.ENRICHED
        except Exception as e:
            logger.warning("Contact enrich failed for '%s': %s", lead.name, e)
        # Address validation (US Census geocoder, free) — runs in this same pool
        # so the network call overlaps other leads' crawls. US-only.
        if lead.address and region == "US":
            try:
                from backend.services.verifier import verify_address
                lead.address_valid = verify_address(lead.address)
            except Exception as e:
                logger.debug("Address validation failed for '%s': %s", lead.name, e)

        # LLM extraction fallback — only where regex left gaps AND we already have
        # the page text in `cache` (no extra fetch). Runs HERE, before _stage_score,
        # so recovered services/owner_title/employee_count actually earn points.
        _llm_extract_gaps(lead, cache)
        return lead

    done = 0
    total = max(len(leads), 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_enrich, l): l for l in leads}
        for fut in as_completed(futures):
            lead = futures[fut]
            done += 1
            pct = pct_lo + int((pct_hi - pct_lo) * done / total)
            _push(job.id, pct, "Enriching",
                  f"Enriched {done}/{total}: {lead.name}", scraped=len(leads))
    db.commit()


def _llm_extract_gaps(lead, cache: dict) -> None:
    """Fill fields regex missed, from page text we already fetched.

    Gated on ENABLE_LLM_EXTRACTION. Deliberately conservative:
      - fires only when a field is EMPTY and only over cached page text
      - never raises data_confidence — that score is reserved for independently
        verified signals, and an LLM reading a page is not verification
      - every filled field is stamped with a "llm" provenance marker where the
        model has one (employee_count_source), mirroring the existing pattern
    """
    if not getattr(settings, "ENABLE_LLM_EXTRACTION", False):
        return
    gaps = [f for f in ("services", "owner_name", "owner_title",
                        "employee_count", "year_founded")
            if not getattr(lead, f, None)]
    if not gaps or not cache:
        return
    try:
        from backend.services.business_intel import _visible_text
        from backend.services.pitch_research import extract_facts

        key = settings.NVIDIA_API_KEY.strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        if not key:
            return
        html = next((h for h in cache.values() if h), None)   # homepage is fetched first
        if not html:
            return

        got = extract_facts(
            lead.name or "", _visible_text(html), api_key=key,
            base_url=settings.NVIDIA_BASE_URL,
            models=[m.strip() for m in settings.NVIDIA_MODELS.split(",") if m.strip()],
        )
        filled = []
        for f in gaps:
            if f in got:
                setattr(lead, f, got[f])
                filled.append(f)
        if "employee_count" in filled:
            lead.employee_count_source = "llm"
        if filled:
            logger.info("LLM extraction filled %s for '%s'", filled, lead.name)
    except Exception as e:  # noqa: BLE001 — never fail a lead over an optional extra
        logger.debug("LLM extraction skipped for '%s': %s", getattr(lead, "name", "?"), e)


def _stage_score(db, job: Job, leads: list):
    """
    Score each lead using a weighted rubric with confidence awareness.

    Scoring rubric (max 100):
      CONTACT INFO (40 pts):
        - Email (personal/on-domain):       30
        - Email (medium tier):              20
        - Email (role/fallback):            10 / 5
        - Valid phone (formatted):          10
      WEBSITE (25 pts):
        - Has website + active status:      15
        - CMS detected:                      5
        - SSL valid:                         5
      SOCIAL PROOF (20 pts):
        - Google Maps >= 4.0 rating:        15
        - Google Maps presence (any):        5
        - Social media (FB/IG/TW per):      5 (max 10)
      BONUS (15 pts):
        - Owner name found:                  5
        - High review count (>50):           5
        - Google says open:                  5
      BUSINESS INTELLIGENCE (10 pts max):
        - Employee count found:              5
        - Year founded found:                3
        - Partners/clients found:            2
      DEEP RESEARCH (7 pts max):
        - Services/products found:           2
        - Team members extracted:            3
        - Owner/decision-maker title found:  2
      REVIEW WEAKNESSES (penalty):
        - Per weakness detected:            -1
    """
    from backend.utils.validator import COUNTRY_TO_REGION

    phone_region = COUNTRY_TO_REGION.get(job.country, "US")

    for lead in leads:
        breakdown = {}
        score = 0

        # ── CONTACT INFO (40 pts max) ──
        email_score = 0
        if lead.owner_email:
            local = lead.owner_email.split("@")[0].lower()
            domain = lead.owner_email.split("@")[1].lower() if "@" in lead.owner_email else ""
            email_domain = domain
            on_domain = lead.website and domain in lead.website.lower()

            # Determine email quality tier
            is_personal = re.match(r"^[a-z][a-z.]+[a-z]$", local) and len(local) >= 4
            is_role = local in {
                "info", "contact", "support", "admin", "hello", "sales",
                "office", "team", "mail", "enquiries", "booking",
            }
            is_generic = email_domain in {
                "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
                "icloud.com", "mail.com", "protonmail.com", "proton.me", "zoho.com",
            }

            if is_personal and on_domain:
                email_score = 30  # Best: first.last@company.com
            elif is_personal and not on_domain:
                email_score = 20  # Personal but on generic domain
            elif not is_role and on_domain:
                email_score = 20  # On-domain but non-personal local
            elif is_role and on_domain:
                email_score = 10  # info@company.com
            elif not is_generic:
                email_score = 10  # Some other business domain
            else:
                email_score = 5   # Fallback

        breakdown["email"] = email_score
        score += email_score

        # ── EMAIL VERIFICATION BONUS (MX + RCPT deliverability) ──
        # verify_email is cached per-address (already called during enrichment),
        # so this is a free cache hit — no second SMTP round-trip.
        email_verify_bonus = 0
        if lead.owner_email:
            from backend.services.verifier import verify_email
            ev = verify_email(lead.owner_email, timeout=3)
            if ev.get("mx_valid"):
                email_verify_bonus += 4
            if ev.get("deliverable") is True:
                email_verify_bonus += 4      # proven mailbox (RCPT 250, not catch-all)
            elif ev.get("smtp_valid"):
                email_verify_bonus += 2      # server reachable but not proven deliverable
            if not ev.get("is_role") and ev.get("mx_valid"):
                email_verify_bonus += 2
        breakdown["email_verify"] = email_verify_bonus
        score += email_verify_bonus

        phone_valid = False
        phone_type = "unknown"
        if lead.phone:
            from backend.services.verifier import verify_phone
            v = verify_phone(lead.phone, region=phone_region)
            if v["valid"]:
                lead.phone_formatted = v["national"]
                phone_valid = True
                phone_type = v["type"]

        phone_score = 10 if phone_valid else 0
        if phone_type in ("mobile", "voip", "toll_free"):
            phone_score += 2
        breakdown["phone"] = phone_score
        score += phone_score

        # ── WEBSITE (25 pts max) ──
        # Categorise every lead (was only set on HAS_WEBSITE, so the "No Website
        # (Hot)" deliverable sheet/PDF section was always empty). An active site
        # = HAS_WEBSITE; no site (or a dead one with no URL) = NO_WEBSITE.
        has_active_site = bool(lead.website) and lead.website_status == WebsiteStatus.ACTIVE
        lead.category = LeadCategory.HAS_WEBSITE if has_active_site else LeadCategory.NO_WEBSITE

        web_score = 0
        if lead.website:
            if lead.website_status == WebsiteStatus.ACTIVE:
                web_score = 15
            elif lead.website_status == WebsiteStatus.NONE:
                web_score = 0
            else:
                web_score = 5  # PARKED/ERROR but has a URL
        breakdown["website_active"] = web_score
        score += web_score

        if lead.website_cms:
            breakdown["cms"] = 5
            score += 5

        if lead.has_ssl:
            breakdown["ssl"] = 5
            score += 5

        # ── SOCIAL PROOF (20 pts max) ──
        maps_score = 0
        if lead.has_google_maps:
            maps_score += 5  # Base presence
            if lead.google_rating and lead.google_rating >= 4.0:
                maps_score += 10  # High rating bonus
            elif lead.google_rating and lead.google_rating >= 3.0:
                maps_score += 5   # Decent rating
        breakdown["google_maps"] = maps_score
        score += maps_score

        social_score = 0
        social_count = 0
        if lead.social_facebook:
            social_count += 1
        if lead.social_instagram:
            social_count += 1
        if lead.social_twitter:
            social_count += 1
        social_score = min(social_count * 5, 10)
        breakdown["social"] = social_score
        score += social_score

        # ── BONUS (15 pts max) ──
        bonus = 0
        if lead.owner_name:
            bonus += 5
        if lead.owner_phone:                      # owner/direct line found
            bonus += 5
        elif lead.google_review_count and lead.google_review_count > 50:
            bonus += 5
        if lead.google_is_open:
            bonus += 5
        breakdown["bonus"] = bonus
        score += bonus

        # ── BUSINESS INTELLIGENCE (10 pts max) ──
        intel_bonus = 0
        if lead.employee_count and lead.employee_count > 0:
            if lead.employee_count > 50:
                intel_bonus += 5
            elif lead.employee_count > 10:
                intel_bonus += 3
            else:
                intel_bonus += 1
        if lead.year_founded and lead.year_founded > 1900:
            intel_bonus += 3
        if lead.partners:
            n_partners = len(lead.partners.split(",") if isinstance(lead.partners, str) else lead.partners)
            if n_partners >= 3:
                intel_bonus += 2
            elif n_partners >= 1:
                intel_bonus += 1
        breakdown["intel"] = intel_bonus
        score += intel_bonus

        # ── DEEP RESEARCH (7 pts max) ──
        research = 0
        if lead.services:                       research += 2
        if lead.team_members:                   research += 3
        if lead.owner_title:                    research += 2
        breakdown["research"] = research
        score += research

        # ── REVIEW WEAKNESSES (penalty) ──
        weakness_count = getattr(lead, 'review_weakness_count', 0)
        if not isinstance(weakness_count, int):
            weakness_count = 0
        weakness_penalty = min(weakness_count, 5)  # cap at -5
        if weakness_penalty > 0:
            breakdown["weakness_penalty"] = -weakness_penalty
            score -= weakness_penalty

        # ── CAP at 100, store ──
        lead.lead_score = min(score, 100)
        lead.status = LeadStatus.VALIDATED

        # ── Overall data confidence (0-1): how trustworthy/complete is this lead ──
        conf = 0.0
        if lead.phone_verified:                 conf += 0.20
        elif lead.phone:                        conf += 0.05
        if lead.email_verified:                 conf += 0.20
        elif lead.owner_email:                  conf += 0.05
        if lead.website_status == WebsiteStatus.ACTIVE: conf += 0.15
        if lead.website_name_found:             conf += 0.08
        if lead.has_google_maps:                conf += 0.15
        if lead.owner_name:                     conf += 0.10
        if lead.owner_phone:                    conf += 0.10 * float(lead.owner_phone_confidence or 0)
        # business intelligence corroboration
        if lead.employee_count:                  conf += 0.05
        if lead.year_founded:                    conf += 0.05
        if lead.partners:                        conf += 0.05
        # deep research corroboration
        if lead.services:                        conf += 0.05
        if lead.team_members:                    conf += 0.08
        if lead.owner_title:                     conf += 0.05
        # multi-source corroboration
        n_sources = len((lead.sources or "").split(",")) if lead.sources else 0
        if n_sources >= 2:                       conf += 0.10
        if n_sources >= 3:                       conf += 0.05
        # Phone confirmed by >= 2 independent sources = strong corroboration.
        psc = getattr(lead, "phone_source_count", 0)
        if isinstance(psc, int) and psc >= 2:    conf += 0.05
        if lead.address_valid is True:           conf += 0.05
        lead.data_confidence = round(min(conf, 1.0), 2)

        # ── Verification tier (honest label for the export) ──
        # verified      = an independently-checked contact channel (phone valid /
        #                 email RCPT-or-MX verified).
        # corroborated  = no verified channel, but on Google Maps AND confirmed by
        #                 >= 2 independent sources.
        # unverified    = neither — only ships via the never-empty fallback.
        if lead.phone_verified or lead.email_verified:
            lead.verification_tier = "verified"
        elif lead.has_google_maps and n_sources >= 2:
            lead.verification_tier = "corroborated"
        else:
            lead.verification_tier = "unverified"

        logger.info(
            "Score for %s: %d | email=%d phone=%d web=%d maps=%d social=%d bonus=%d intel=%d research=%d weakness=%d",
            lead.name, lead.lead_score,
            breakdown.get("email", 0), breakdown.get("phone", 0),
            breakdown.get("website_active", 0), breakdown.get("google_maps", 0),
            breakdown.get("social", 0), breakdown.get("bonus", 0),
            breakdown.get("intel", 0), breakdown.get("research", 0),
            breakdown.get("weakness_penalty", 0),
        )

    db.commit()


def _stage_reviews(db, job: Job, leads: list):
    """Goal 4a — harvest real review content for the export slice: worst-first
    review texts, histogram, true last-review date, owner-response presence. Then
    replace the arithmetic review_weaknesses with evidence-based ones. Soft-fail."""
    import json as _json
    from backend.services.review_scraper import (
        scrape_reviews_batch_sync, summarize_review_weaknesses,
    )

    targets = [l for l in leads
               if l.google_maps_url and (l.google_review_count or 0) > 0]
    if not targets:
        return
    leads_data = [{"id": l.id, "google_maps_url": l.google_maps_url} for l in targets]

    def on_progress(done, total):
        _push(job.id, 93, "Reviews", f"Deep review scan {done}/{total}")

    try:
        results = scrape_reviews_batch_sync(leads_data, on_progress=on_progress)
    except Exception as e:
        logger.warning("Review scrape failed: %s", e)
        return

    # A soft-block looks exactly like "no reviews" unless we say otherwise: Maps
    # serves a panel with a rating but no review count, every harvest comes back
    # empty, and the job silently records a false negative on every lead.
    blocked = sum(1 for d in results.values() if d and d.get("unavailable"))
    if blocked and blocked >= max(1, len(results) // 2):
        logger.warning(
            "Review scrape: %d/%d places served a degraded panel — Google is "
            "throttling this IP. Review evidence for this job is INCOMPLETE; "
            "re-run later or route through a working proxy.", blocked, len(results))
        _push(job.id, 93, "Reviews",
              f"Reviews unavailable for {blocked}/{len(results)} leads (rate-limited)")

    by_id = {l.id: l for l in targets}
    for lead_id, data in results.items():
        lead = by_id.get(lead_id)
        if not lead or not data:
            continue
        reviews = data.get("reviews") or []
        if reviews:
            lead.review_lowest_texts = _json.dumps(reviews, ensure_ascii=False)
        if data.get("histogram"):
            lead.rating_histogram = _json.dumps(data["histogram"])
        lead.last_review_date = data.get("last_review_date")
        lead.owner_responds = data.get("owner_responds")
        # Evidence-based weaknesses replace the rating-arithmetic proxy.
        wk_text, wk_count = summarize_review_weaknesses(
            reviews, data.get("histogram"), data.get("owner_responds"))
        if wk_text:
            lead.review_weaknesses = wk_text
            lead.review_weakness_count = wk_count
    db.commit()


def _stage_pitch_research(leads: list):
    """LLM pitch synthesis over the export slice — writes pitch_angle /
    pain_points / opener / review_themes in place. No DB access (the caller
    commits), so it is safe to run on a worker thread. Soft-fail."""
    try:
        from backend.services.pitch_research import research_leads_sync
        research_leads_sync(leads, model=settings.OLLAMA_MODEL, url=settings.OLLAMA_URL)
    except Exception as e:
        logger.warning("pitch research skipped: %s", e)


def _stage_registration(job: Job, leads: list):
    """Goal 4b — legal-entity lookup for the export slice. Fills legal_name /
    entity_status / registration_date etc., nudges confidence up for an active
    registration, and hard-rejects a dissolved one (drops it below the export
    gate). Soft-fail; runs in a small thread pool since each is a network call.

    No DB access — the caller commits on the main thread (see run_pipeline)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from backend.services.registration import lookup_registration
    from backend.services.verifier import _area_code_to_state

    def _one(lead):
        state = _area_code_to_state(lead.phone or "") if lead.phone else None
        reg = lookup_registration(lead.name or "", state)
        if not reg:
            return
        lead.legal_name = reg.get("legal_name")
        lead.entity_type = reg.get("entity_type")
        lead.entity_status = reg.get("entity_status")
        lead.registration_date = reg.get("registration_date")
        lead.registered_agent = reg.get("registered_agent")
        lead.registry_source = reg.get("registry_source")
        # Feed accuracy: active registration corroborates; dissolved is a reject.
        if reg.get("entity_status") == "active":
            lead.data_confidence = round(min((lead.data_confidence or 0) + 0.10, 1.0), 2)
        elif reg.get("entity_status") == "dissolved":
            lead.data_confidence = round((lead.data_confidence or 0) * 0.3, 2)
            lead.verification_tier = "unverified"

    workers = max(1, min(settings.CONTACT_CONCURRENCY, len(leads) or 1))
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(_one, l) for l in leads]):
                try:
                    fut.result()
                except Exception as e:
                    logger.debug("Registration lookup failed: %s", e)
    except Exception as e:
        logger.warning("Registration stage failed: %s", e)


def _stage_export(db, job: Job, leads: list, total_scraped: int = 0, total_verified: int = 0):
    import json
    from backend.services.pdf_builder import build_pdf
    from backend.services.xlsx_builder import build_xlsx
    from backend.utils.naming import export_dirname

    # Readable folder name (e.g. Medical_clinic_Los_Angeles_CA_2026-06-22_50e721a7)
    # instead of the raw job UUID.
    export_dir = settings.EXPORTS_DIR / export_dirname(job)
    export_dir.mkdir(parents=True, exist_ok=True)

    xlsx_path = build_xlsx(leads, export_dir, job)
    pdf_path  = build_pdf(leads, export_dir, job)

    # ponytail: per-export index — soft-fail so a manifest error never fails the job.
    try:
        manifest = _build_manifest(job, leads, total_scraped, total_verified, {
            "xlsx": xlsx_path.name if xlsx_path else None,
            "pdf": pdf_path.name if pdf_path else None,
            "manifest": "manifest.json",
        })
        (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    except Exception as e:
        logger.warning("manifest write failed (non-fatal): %s", e)

    return xlsx_path, pdf_path

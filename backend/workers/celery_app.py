"""
workers/celery_app.py — Celery instance.
"""
import logging

from celery import Celery
from celery.signals import worker_ready

from backend.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "lead_generator",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["backend.workers.pipeline"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # acks_late: the task is acknowledged only when it FINISHES. With acks_late
    # False a killed worker (OOM, restart, laptop sleep) lost the task from the
    # broker while the Job row stayed RUNNING forever. Combined with
    # reject_on_worker_lost the interrupted run is redelivered instead.
    # Safe because the scrape stage is idempotent — it wipes the job's leads and
    # rebuilds them in one transaction (see _stage_scrape).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    # A hung crawl can't stall a worker forever: soft raises SoftTimeLimitExceeded
    # (caught by the pipeline's except → job marked FAILED), hard SIGKILLs.
    task_soft_time_limit=7200,
    task_time_limit=7800,
    # NVIDIA pitch-research rate limiting (36 rpm) is enforced by a PER-PROCESS
    # token bucket (pitch_research._NVIDIA_LIMITER). Two worker processes each get
    # their own bucket, so their combined rate can exceed the 40 rpm free budget
    # and trip 429s. Run the pipeline worker single-process — `celery -A
    # backend.workers.celery_app worker -c 1` — until the limiter is moved to
    # Redis. concurrency=1 is fine here: a job is one long task, not many small
    # ones, and internal parallelism (enrich pools, PITCH_CONCURRENCY) still runs.
    worker_concurrency=1,
)


@worker_ready.connect
def _reconcile_on_worker_start(**_kw):
    """Sweep jobs orphaned by a previous worker death as soon as a worker is up.

    The API does the same on its own startup; whichever runs first wins and the
    other is a no-op.
    """
    try:
        from backend.workers.pipeline import reconcile_stale_jobs
        n = reconcile_stale_jobs()
        if n:
            logger.warning("Worker startup reconciled %d stale job(s)", n)
    except Exception as e:  # never block worker startup
        logger.warning("Worker-start reconciliation skipped: %s", e)

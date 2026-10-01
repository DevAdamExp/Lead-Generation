"""
api/jobs.py — Job CRUD endpoints.
"""
import asyncio
import logging
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pathlib import Path

logger = logging.getLogger(__name__)

from backend.database import get_async_db
from backend.models import Job, JobStatus
from backend.schemas import JobCreate, JobResponse
from backend.config import settings

router = APIRouter()


@router.post("/jobs", response_model=JobResponse, status_code=202)
async def create_job(
    payload: JobCreate,
    db: AsyncSession = Depends(get_async_db),
):
    job = Job(
        id=str(uuid.uuid4()),
        niche=payload.niche,
        location=payload.location,
        country=payload.country,
        limit=payload.limit,
        status=JobStatus.PENDING,
        current_stage="Queued",
        stage_message="Job queued — starting pipeline...",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue SYNCHRONOUSLY. This used to be a BackgroundTask, which runs after
    # the response is sent — so when Redis/Celery was unreachable, .delay() raised
    # where nobody could see it, the client got 202, and the job sat at PENDING
    # forever (four such rows were found 16 days old). Fail loudly instead.
    # .delay() is blocking I/O, so keep it off the event loop.
    try:
        await asyncio.to_thread(_enqueue_pipeline, job.id)
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.current_stage = "Failed"
        job.stage_message = "Could not queue the job — is Redis/Celery running?"
        job.error_message = f"enqueue failed: {exc}"[:1000]
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="Job queue unavailable — the job was not started. "
                   "Check that Redis and the Celery worker are running.",
        ) from exc

    return job


def _enqueue_pipeline(job_id: str):
    from backend.workers.pipeline import run_pipeline
    run_pipeline.delay(job_id)


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(Job).order_by(Job.created_at.desc()).limit(50))
    return result.scalars().all()


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_async_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/export")
async def export_job(job_id: str, format: str = "xlsx", db: AsyncSession = Depends(get_async_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Job not yet completed")

    from backend.utils.naming import export_filebase
    base = export_filebase(job)

    if format == "pdf" and job.export_pdf_path:
        path = Path(job.export_pdf_path)
        if path.exists():
            return FileResponse(path, filename=f"{base}.pdf", media_type="application/pdf")

    if job.export_xlsx_path:
        path = Path(job.export_xlsx_path)
        if path.exists():
            return FileResponse(
                path,
                filename=f"{base}.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    raise HTTPException(status_code=404, detail="Export file not ready")


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, db: AsyncSession = Depends(get_async_db)):
    """Delete a job, its leads, and its export folder.

    The export folder used to be left behind: deleting a job orphaned its
    XLSX/PDF on disk with no DB row pointing at them (three such folders were
    found holding 175 delivered leads). The delivered_businesses record is
    deliberately NOT removed — that a business was already handed to the user
    stays true regardless of whether the job row survives.
    """
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from backend.utils.naming import export_dirname
    export_dir = settings.EXPORTS_DIR / export_dirname(job)

    await db.delete(job)
    await db.commit()

    try:
        # Guard against a crafted niche/location escaping the exports root.
        # slugify() already strips separators, but verify before an rmtree.
        resolved = export_dir.resolve()
        if resolved.is_dir() and resolved.parent == settings.EXPORTS_DIR.resolve():
            shutil.rmtree(resolved)
            logger.info("Removed export folder for deleted job %s: %s", job_id, resolved)
    except Exception as e:
        logger.warning("Could not remove export folder for job %s: %s", job_id, e)

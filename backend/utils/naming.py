"""
utils/naming.py — human-readable names for export folders and download files.

Export folders used to be the raw job UUID (e.g. exports/50e721a7-…/). These
helpers produce readable, filesystem-safe names like
    Medical_clinic_Los_Angeles_CA_2026-06-22_50e721a7
while keeping a short job-id suffix so repeat runs never collide.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


def slugify(value: str, maxlen: int = 40) -> str:
    """Filesystem-safe slug: alnum runs joined by underscores."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_")
    return slug[:maxlen].strip("_") or "leads"


def export_dirname(job) -> str:
    """Readable, unique export folder name for a job."""
    created = getattr(job, "created_at", None) or datetime.now(timezone.utc)
    date = created.strftime("%Y-%m-%d")
    short = (getattr(job, "id", "") or "")[:8]
    return f"{slugify(job.niche)}_{slugify(job.location)}_{date}_{short}".strip("_")


def export_filebase(job) -> str:
    """Readable base name for the downloaded file (no extension)."""
    return f"{slugify(job.niche)}_{slugify(job.location)}_leads"

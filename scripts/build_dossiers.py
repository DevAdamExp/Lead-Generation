#!/usr/bin/env python3
"""build_dossiers.py — one research-dossier PDF per delivered lead of a job.

backend/services/lead_dossier.py had no caller anywhere in the product: the
feature was only ever driven from throwaway scratch scripts, so it was
unreachable and invisible to anyone reading the repo. This is its entry point.

Each PDF covers, for one lead:
  1. what the business is and what it actually sells
  2. weaknesses its reviews / reputation expose
  3. what we can pitch it
  4. market position — competitors, and where they are beating it
  5. a concrete pitch plan

Competitors are the OTHER leads of the same job (same niche + city), with their
real scraped ratings and review counts, so comparisons cite actual numbers.

Usage:
    python scripts/build_dossiers.py --latest
    python scripts/build_dossiers.py <job_id>
    python scripts/build_dossiers.py --latest --limit 3      # try a few first
    python scripts/build_dossiers.py --list                  # show recent jobs

Requires NVIDIA_API_KEY (see .env). Roughly one LLM call and one page fetch per
lead — budget ~60s each.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings                                 # noqa: E402
from backend.database import SyncSessionLocal                       # noqa: E402
from backend.models import Job, JobStatus, Lead                     # noqa: E402
from backend.services.lead_dossier import (                         # noqa: E402
    build_dossier, fetch_page_text, render_dossier_pdf,
)
from backend.utils.naming import slugify                            # noqa: E402
from backend.workers.pipeline import select_export_leads            # noqa: E402


def _nvidia_config() -> tuple[str, str, list[str]]:
    key = (settings.NVIDIA_API_KEY or "").strip()
    if key.lower().startswith("bearer "):      # tolerate a pasted "Bearer …"
        key = key[7:].strip()
    if not key:
        sys.exit("NVIDIA_API_KEY is not set — add it to .env first.")
    models = [m.strip() for m in (settings.NVIDIA_MODELS or "").split(",") if m.strip()]
    return key, settings.NVIDIA_BASE_URL, models


def list_jobs(db) -> None:
    rows = (db.query(Job).order_by(Job.created_at.desc()).limit(15)).all()
    if not rows:
        print("no jobs in the database")
        return
    print(f"{'job id':38} {'status':10} {'leads':>6}  niche / location")
    for j in rows:
        n = db.query(Lead).filter(Lead.job_id == j.id).count()
        print(f"{j.id:38} {j.status.value:10} {n:>6}  {j.niche} / {j.location}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_id", nargs="?", help="job to build dossiers for")
    ap.add_argument("--latest", action="store_true",
                    help="use the most recent completed job")
    ap.add_argument("--list", action="store_true", help="list recent jobs and exit")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the top N delivered leads (0 = all)")
    args = ap.parse_args()

    db = SyncSessionLocal()
    try:
        if args.list:
            list_jobs(db)
            return 0

        if args.latest:
            job = (db.query(Job).filter(Job.status == JobStatus.COMPLETED)
                     .order_by(Job.created_at.desc()).first())
            if job is None:
                sys.exit("no completed job found — run a search first")
        elif args.job_id:
            job = db.get(Job, args.job_id)
            if job is None:
                sys.exit(f"job {args.job_id} not found (try --list)")
        else:
            ap.error("give a job id, or --latest (or --list)")

        peers = db.query(Lead).filter(Lead.job_id == job.id).all()
        if not peers:
            sys.exit(f"job {job.id} has no leads")
        targets = select_export_leads(peers, max(1, job.limit or 10))
        if args.limit:
            targets = targets[:args.limit]

        key, base_url, models = _nvidia_config()
        out_dir = settings.EXPORTS_DIR / (
            f"Dossiers_{slugify(job.niche)}_{slugify(job.location)}")

        print(f"{job.niche} / {job.location} — {len(peers)} leads, "
              f"{len(targets)} dossiers to build")
        t0, made = time.perf_counter(), []
        for i, lead in enumerate(targets, 1):
            ts = time.perf_counter()
            page_text = fetch_page_text(lead)
            try:
                d = build_dossier(lead, peers, api_key=key, base_url=base_url,
                                  models=models, page_text=page_text)
                path = render_dossier_pdf(lead, d, peers, out_dir)
                made.append(path)
                print(f"  [{i}/{len(targets)}] {str(lead.name)[:34]:34} "
                      f"{time.perf_counter()-ts:5.1f}s  "
                      f"page={'Y' if page_text else 'N'}  -> {path.name}", flush=True)
            except Exception as e:                       # noqa: BLE001 — per-lead soft-fail
                print(f"  [{i}/{len(targets)}] {str(lead.name)[:34]:34} "
                      f"FAILED {type(e).__name__}: {str(e)[:60]}", flush=True)

        print(f"\n{len(made)}/{len(targets)} dossiers in "
              f"{(time.perf_counter()-t0)/60:.1f} min")
        print(f"written to {out_dir}")
        return 0 if made else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

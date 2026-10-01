"""End-to-end lifecycle test — exercises the real stack and writes a PDF report.

Covers the happy path AND the failure modes the audits fixed:
crash recovery, timeout salvage, broker outage, job deletion, cross-run dedup,
data-quality gates, API surface, and circuit isolation.

Usage:
    python scripts/e2e_lifecycle_test.py <live_job_id>
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

API = "http://localhost:8000/api"
RESULTS: list[dict] = []


def check(name: str, group: str, passed: bool, detail: str = "", evidence: str = ""):
    RESULTS.append({"name": name, "group": group, "passed": bool(passed),
                    "detail": detail, "evidence": evidence})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    return passed


# ── 1. Job creation + queue ───────────────────────────────────────────────────

def test_broker_outage_is_reported(job_id_unused=None):
    """P0-3: enqueue is synchronous, so a dead broker must surface as 503 and a
    FAILED job — not a 202 and a row that sits PENDING forever."""
    from backend.api.jobs import _enqueue_pipeline
    import backend.api.jobs as jobs_mod

    orig = jobs_mod._enqueue_pipeline
    try:
        def boom(_jid):
            raise ConnectionError("simulated: broker unreachable")
        jobs_mod._enqueue_pipeline = boom
        # Call the endpoint through the running server? No — the server holds its
        # own module. Verify the handler's contract directly instead.
        import asyncio
        from backend.database import AsyncSessionLocal
        from backend.models import Job, JobStatus
        from backend.schemas import JobCreate
        from fastapi import HTTPException

        async def run():
            async with AsyncSessionLocal() as db:
                try:
                    await jobs_mod.create_job(
                        JobCreate(niche="broker-test", location="Nowhere, XX",
                                  country="us", limit=5), db)
                    return None, None
                except HTTPException as e:
                    row = (await db.execute(
                        __import__("sqlalchemy").select(Job)
                        .where(Job.niche == "broker-test")
                        .order_by(Job.created_at.desc()).limit(1))).scalar()
                    return e.status_code, row

        code, row = asyncio.run(run())
        ok = code == 503 and row is not None and row.status == JobStatus.FAILED
        check("Broker outage returns 503 and marks the job FAILED", "Job lifecycle", ok,
              f"HTTP {code}, job status {getattr(row, 'status', None)}",
              f"stage_message: {getattr(row, 'stage_message', '')}")

        # Clean up after ourselves. This check deliberately CREATES a job row, and
        # without this every run left another "broker-test / Nowhere, XX" behind
        # in the real database (four had accumulated). Tests must not silt up the
        # data they run against.
        from backend.database import SyncSessionLocal
        db = SyncSessionLocal()
        try:
            for j in db.query(Job).filter(Job.niche == "broker-test").all():
                db.delete(j)
            db.commit()
        finally:
            db.close()
    finally:
        jobs_mod._enqueue_pipeline = orig


# ── 2. Crash recovery ─────────────────────────────────────────────────────────

def test_stale_job_reconciliation():
    """P0-1: a job whose worker died must not stay RUNNING forever."""
    from backend.database import SyncSessionLocal
    from backend.models import Job, JobStatus
    from backend.workers.pipeline import reconcile_stale_jobs, STALE_AFTER_SECONDS

    db = SyncSessionLocal()
    old = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 300)
    dead = Job(id=str(uuid.uuid4()), niche="crash-test", location="Nowhere, XX",
               limit=5, status=JobStatus.RUNNING, created_at=old, heartbeat_at=old,
               current_stage="Enriching", progress_pct=45)
    alive = Job(id=str(uuid.uuid4()), niche="alive-test", location="Nowhere, XX",
                limit=5, status=JobStatus.RUNNING,
                created_at=datetime.now(timezone.utc),
                heartbeat_at=datetime.now(timezone.utc), progress_pct=45)
    db.add_all([dead, alive])
    db.commit()

    reconcile_stale_jobs()
    db.expire_all()
    dead_now, alive_now = db.get(Job, dead.id), db.get(Job, alive.id)

    check("Dead worker's job is reconciled to FAILED", "Crash recovery",
          dead_now.status == JobStatus.FAILED,
          f"status {dead_now.status.value}", dead_now.stage_message or "")
    check("A live job with a fresh heartbeat is left running", "Crash recovery",
          alive_now.status == JobStatus.RUNNING,
          "not killed by the sweep")

    for j in (dead_now, alive_now):
        db.delete(j)
    db.commit()
    db.close()


def test_acks_late_config():
    from backend.workers.celery_app import celery_app as c
    from backend.workers.pipeline import STALE_AFTER_SECONDS
    check("acks_late on — interrupted task is redelivered", "Crash recovery",
          c.conf.task_acks_late is True and c.conf.task_reject_on_worker_lost is True,
          f"acks_late={c.conf.task_acks_late}")
    check("Stale threshold exceeds Celery hard limit", "Crash recovery",
          STALE_AFTER_SECONDS > c.conf.task_time_limit,
          f"{STALE_AFTER_SECONDS}s > {c.conf.task_time_limit}s")


def test_resume_skips_completed_work():
    """P2-3: a redelivered task must not redo finished leads."""
    from types import SimpleNamespace
    from backend.models import LeadStatus
    import backend.workers.pipeline as p

    enriched = []
    saved = {k: getattr(p, k) for k in
             ("_stage_maps_plan", "_stage_maps_fetch", "_stage_maps_apply",
              "_stage_websites", "_stage_intel_and_contacts", "_stage_score",
              "_push", "_update_job")}
    try:
        p._stage_maps_plan = lambda job, b: []
        p._stage_maps_fetch = lambda job, a: {}
        p._stage_maps_apply = lambda *a: None
        p._stage_websites = lambda db, job, b: None
        p._stage_intel_and_contacts = lambda db, job, b, **kw: enriched.extend(b)
        def _score(db, job, batch):
            for l in batch:
                l.status, l.lead_score = LeadStatus.VALIDATED, 80
        p._stage_score = _score
        p._push = lambda *a, **k: None
        p._update_job = lambda db, job, **kw: None

        def L(status, **kw):
            base = dict(status=status, lead_score=80 if status == LeadStatus.VALIDATED else 0,
                        data_confidence=0.9, phone_verified=True, has_google_maps=True,
                        sources="google_maps", email_verified=False, phone="1",
                        owner_email=None, website=None)
            base.update(kw)
            return SimpleNamespace(**base)

        raw = dict(phone_verified=False, has_google_maps=False)
        leads = [L(LeadStatus.VALIDATED), L(LeadStatus.VALIDATED),
                 L(LeadStatus.SCRAPED, **raw), L(LeadStatus.SCRAPED, **raw)]
        p._enrich_until_target(None, __import__("types").SimpleNamespace(
            id="j", location="x", country="us"), leads, target=4)
        check("Resume re-enriches only unfinished leads", "Crash recovery",
              len(enriched) == 2, f"{len(enriched)} of 4 re-enriched (2 were done)")
    finally:
        for k, v in saved.items():
            setattr(p, k, v)


# ── 3. Timeout salvage ────────────────────────────────────────────────────────

def test_timeout_handler_exists():
    """P0-2: SoftTimeLimitExceeded must be caught BEFORE the generic handler."""
    import inspect
    from celery.exceptions import SoftTimeLimitExceeded
    import backend.workers.pipeline as p

    src = inspect.getsource(p.run_pipeline)
    soft_at = src.find("except SoftTimeLimitExceeded")
    generic_at = src.find("except Exception as exc")
    ok = soft_at != -1 and soft_at < generic_at
    check("Soft-timeout caught before the generic handler", "Timeout salvage", ok,
          "partial export instead of total loss")
    check("SoftTimeLimitExceeded subclasses Exception (why it was swallowed)",
          "Timeout salvage", issubclass(SoftTimeLimitExceeded, Exception),
          "generic except would otherwise catch it")
    check("Timeout path exports before failing", "Timeout salvage",
          "_stage_export" in src[soft_at:generic_at] if ok else False,
          "salvages leads that already cleared the gate")


# ── 4. Delete semantics + durable dedup ───────────────────────────────────────

def test_delete_removes_exports_but_keeps_delivery_record():
    """P0-4: deleting a job must clean its files but NOT make its businesses
    sellable again."""
    import asyncio
    from sqlalchemy import select
    from backend.config import settings
    from backend.database import AsyncSessionLocal
    from backend.models import Job, Lead, DeliveredBusiness, JobStatus
    from backend.utils.naming import export_dirname
    import backend.api.jobs as jobs_mod

    jid = str(uuid.uuid4())

    async def setup_and_delete():
        async with AsyncSessionLocal() as db:
            job = Job(id=jid, niche="delete-test", location="Testville, TX", limit=5,
                      status=JobStatus.COMPLETED, created_at=datetime.now(timezone.utc))
            db.add(job)
            db.add(Lead(id=str(uuid.uuid4()), job_id=jid, name="Delete Me Roofing",
                        phone="(555) 010-2030"))
            await db.commit()
            await db.refresh(job)
            d = settings.EXPORTS_DIR / export_dirname(job)
            d.mkdir(parents=True, exist_ok=True)
            (d / "leads.xlsx").write_text("x")
            db.add(DeliveredBusiness(id=str(uuid.uuid4()), phone7="0102030",
                                     name_norm="deletemeroofing",
                                     business_name="Delete Me Roofing", job_id=jid,
                                     niche="delete-test", location="Testville, TX"))
            await db.commit()
            existed = d.exists()
            await jobs_mod.delete_job(jid, db)
            leftover = (await db.execute(
                select(DeliveredBusiness).where(DeliveredBusiness.job_id == jid))).scalars().all()
            gone_job = await db.get(Job, jid)
            leads_left = (await db.execute(
                select(Lead).where(Lead.job_id == jid))).scalars().all()
            return existed, d, gone_job, leads_left, leftover

    existed, d, gone_job, leads_left, delivered = asyncio.run(setup_and_delete())
    check("Export folder is removed with the job", "Delete semantics",
          existed and not d.exists(), f"{d.name} deleted")
    check("Job row and its leads are gone", "Delete semantics",
          gone_job is None and not leads_left, "cascade works")
    check("Delivery record SURVIVES deletion (no re-selling)", "Delete semantics",
          len(delivered) == 1,
          f"{len(delivered)} record kept — business stays excluded from future jobs")

    # cleanup
    from backend.database import SyncSessionLocal
    s = SyncSessionLocal()
    for row in s.query(DeliveredBusiness).filter(DeliveredBusiness.job_id == jid).all():
        s.delete(row)
    s.commit(); s.close()


def test_cross_run_dedup_excludes_delivered():
    from backend.database import SyncSessionLocal
    from backend.models import DeliveredBusiness
    from backend.workers.pipeline import _load_delivered_exclude

    db = SyncSessionLocal()
    total = db.query(DeliveredBusiness).count()
    exclude, _ = _load_delivered_exclude(db, "Roofing contractor", "no-such-job")
    row = db.query(DeliveredBusiness).filter(
        DeliveredBusiness.name_norm.isnot(None)).first()
    hit = exclude({"name": row.business_name, "phone": None}) if row else False
    check("Cross-run dedup excludes an already-delivered business", "Delete semantics",
          hit, f"{total} businesses on the do-not-resell list",
          f"probe: {row.business_name if row else 'n/a'}")
    check("A brand-new business is NOT excluded", "Delete semantics",
          not exclude({"name": "Totally Novel Roofing Co 9x7", "phone": "5550001111"}),
          "filter is not over-broad")
    db.close()


# ── 5. Data-quality gates (every one caught a real live defect) ───────────────

def test_data_quality_gates():
    from backend.services.contact_finder import (is_plausible_person_name,
                                                 _is_junk_email, _normalize_email)
    from backend.services.pitch_research import _PLACEHOLDER_RE, _clean_text

    cases = [
        ("Junk owner name rejected ('Form Whether')",
         not is_plausible_person_name("Form Whether")),
        ("Page chrome rejected ('Our Team', 'Risk Management')",
         not is_plausible_person_name("Our Team")
         and not is_plausible_person_name("Risk Management")),
        ("Real people accepted (Michael Ortega, Mary O'Brien)",
         is_plausible_person_name("Michael Ortega")
         and is_plausible_person_name("Mary O'Brien")),
        ("JS fragment rejected as email ('n.d@a.length')",
         _is_junk_email("n.d@a.length")),
        ("Asset host rejected ('fonts.gst@ic.com')",
         _is_junk_email("fonts.gst@ic.com")),
        ("Malformed domain rejected ('b@.bing.com')", _is_junk_email("b@.bing.com")),
        ("URL-shaped email repaired (www. stripped)",
         _normalize_email("info@www.quillridgeroofworks.com") == "info@quillridgeroofworks.com"),
        ("Real emails untouched", not _is_junk_email("jeff@tamarackroofworksco.com")
         and _normalize_email("a@x.io") == "a@x.io"),
        ("Mail-merge placeholder opener rejected",
         bool(_PLACEHOLDER_RE.search("Hi [Owner's Name], noticed your rating"))),
        ("Genuine bracketed aside kept",
         not _PLACEHOLDER_RE.search("Saw your 4.8 rating [43 reviews]")),
        ("Markdown stripped from model output",
         _clean_text("**Trust Gap**: no SSL") == "Trust Gap: no SSL"),
    ]
    for name, ok in cases:
        check(name, "Data quality gates", ok)


# ── 6. Circuit isolation ──────────────────────────────────────────────────────

def test_circuit_isolation():
    from backend.services.proxy_rotator import _tag_slot
    spread = [_tag_slot(f"maps{w}", 4) for w in range(4)]
    check("Each scraper worker gets its own Tor circuit", "Circuit isolation",
          sorted(spread) == [0, 1, 2, 3], f"maps0..3 -> {spread}")
    code = ("from backend.services.proxy_rotator import _tag_slot as t;"
            "print([t(f'maps{w}',4) for w in range(4)])")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True).stdout.strip() for _ in range(3)}
    check("Mapping is identical across process restarts", "Circuit isolation",
          len(runs) == 1, f"3 processes agree: {runs.pop() if len(runs)==1 else runs}")


# ── 7. API surface ────────────────────────────────────────────────────────────

def test_api_surface():
    from backend.models import Lead
    from backend.schemas import LeadResponse
    cols = {c.name for c in Lead.__table__.columns}
    exposed = set(LeadResponse.model_fields)
    check("API exposes every Lead column", "API surface", not (cols - exposed),
          f"{len(cols)} columns, {len(cols & exposed)} exposed",
          f"missing: {sorted(cols - exposed) or 'none'}")
    for f in ("pitch_angle", "pain_points", "opener"):
        check(f"LLM field '{f}' reachable via API", "API surface", f in exposed)

    try:
        h = httpx.get(f"{API}/health", timeout=10).json()
        check("Health endpoint returns ok when services are up", "API surface",
              h["status"] == "ok", f"status={h['status']}",
              json.dumps(h["services"].get("failing", [])))
    except Exception as e:
        check("Health endpoint reachable", "API surface", False, str(e)[:80])


# ── 8. Live job verification ──────────────────────────────────────────────────

def verify_live_job(job_id: str) -> dict:
    from backend.services.contact_finder import is_plausible_person_name, _is_junk_email
    from backend.services.pitch_research import _PLACEHOLDER_RE

    job = httpx.get(f"{API}/jobs/{job_id}", timeout=15).json()
    leads = httpx.get(f"{API}/jobs/{job_id}/leads?per_page=50", timeout=20).json()["items"]

    check("Live job completed", "Live pipeline", job["status"] == "completed",
          f"{job['stage_message']}")
    check("Leads were scraped and verified", "Live pipeline",
          job["total_scraped"] > 0 and job["total_verified"] > 0,
          f"scraped {job['total_scraped']}, verified {job['total_verified']}")

    xlsx, pdf = job.get("export_xlsx_path"), job.get("export_pdf_path")
    check("XLSX export written", "Live pipeline", bool(xlsx) and Path(xlsx).exists(),
          Path(xlsx).name if xlsx else "missing")
    check("PDF export written", "Live pipeline", bool(pdf) and Path(pdf).exists(),
          f"{Path(pdf).stat().st_size//1024} KB" if pdf and Path(pdf).exists() else "missing")

    bad_names = [l["owner_name"] for l in leads
                 if l["owner_name"] and not is_plausible_person_name(l["owner_name"])]
    bad_mails = [l["owner_email"] for l in leads
                 if l["owner_email"] and _is_junk_email(l["owner_email"])]
    bad_open = [l["opener"] for l in leads
                if l.get("opener") and _PLACEHOLDER_RE.search(l["opener"])]
    md = [1 for l in leads for f in ("pitch_angle", "pain_points", "opener")
          if l.get(f) and "**" in str(l[f])]

    check("No junk owner names in delivered data", "Live data quality",
          not bad_names, f"{len(leads)} leads checked", str(bad_names))
    check("No junk emails in delivered data", "Live data quality",
          not bad_mails, f"{len(leads)} leads checked", str(bad_mails))
    check("No placeholder openers", "Live data quality", not bad_open,
          f"{len(leads)} leads checked")
    check("No markdown leaking into export fields", "Live data quality", not md,
          f"{len(leads)} leads checked")

    # The same address appearing on unrelated businesses is a scraping artefact
    # (a shared CSS/JS asset), not a contact. Surfaced because a live run put
    # navig@or.online on two different roofers.
    from collections import Counter
    dupes = {e: n for e, n in Counter(
        l["owner_email"] for l in leads if l.get("owner_email")).items() if n > 1}
    check("No email shared across unrelated businesses", "Live data quality",
          not dupes, f"{len(leads)} leads checked", str(dupes))

    pitched = [l for l in leads if l.get("pitch_angle")]
    check("LLM pitch generated for the delivered slice", "Live data quality",
          len(pitched) > 0, f"{len(pitched)}/{len(leads)} leads have a pitch angle")

    # The export gate ships a subset of the job's leads — read the real count
    # from the manifest rather than assuming every scraped lead was delivered.
    exported = None
    try:
        if xlsx:
            mf = Path(xlsx).parent / "manifest.json"
            if mf.exists():
                exported = json.loads(mf.read_text())["counts"]["exported"]
    except Exception:
        pass
    return {"job": job, "leads": leads, "exported": exported}


# ── Report ────────────────────────────────────────────────────────────────────

_CSS = """
:root{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--ok:#16a34a;--bad:#dc2626;--bg:#f8fafc}
*{box-sizing:border-box}
body{font:13px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     color:var(--ink);margin:0;padding:34px 40px}
h1{font-size:25px;margin:0 0 4px;letter-spacing:-.3px}
h2{font-size:15px;margin:26px 0 9px;padding-bottom:5px;border-bottom:2px solid var(--ink);
   letter-spacing:.2px;text-transform:uppercase}
.sub{color:var(--muted);font-size:12px;margin-bottom:20px}
.kpis{display:flex;gap:10px;margin:18px 0 6px}
.kpi{flex:1;border:1px solid var(--line);border-radius:8px;padding:12px 14px;background:var(--bg)}
.kpi .n{font-size:23px;font-weight:700;line-height:1.1}
.kpi .l{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-top:3px}
table{width:100%;border-collapse:collapse;margin-bottom:6px}
th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);
   padding:7px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.st{font-weight:700;white-space:nowrap;font-size:11px}
.pass{color:var(--ok)} .fail{color:var(--bad)}
.det{color:var(--muted);font-size:11.5px}
.ev{color:var(--muted);font-size:10.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
    word-break:break-all}
.lead{border:1px solid var(--line);border-radius:8px;padding:11px 13px;margin-bottom:9px}
.lead .nm{font-weight:700;font-size:13.5px}
.lead .meta{color:var(--muted);font-size:11px;margin:2px 0 6px}
.lead .row{font-size:11.5px;margin:3px 0}
.lead .k{color:var(--muted);display:inline-block;min-width:74px}
.note{background:var(--bg);border-left:3px solid var(--muted);padding:9px 12px;
      font-size:11.5px;color:#334155;margin:10px 0}
.foot{margin-top:26px;padding-top:10px;border-top:1px solid var(--line);
      color:var(--muted);font-size:10.5px}
@page{size:A4;margin:12mm}
"""


def _esc(v):
    return (str(v) if v is not None else "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


def build_report(live, out_path: Path, suite_line: str, timings: dict):
    total = len(RESULTS); passed = sum(1 for r in RESULTS if r["passed"])
    job = live["job"]; leads = live["leads"]
    groups = {}
    for r in RESULTS:
        groups.setdefault(r["group"], []).append(r)

    h = [f"<style>{_CSS}</style>",
         "<h1>Lead-Generator — End-to-End Lifecycle Test</h1>",
         f"<div class='sub'>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
         f"&nbsp;·&nbsp; live job <b>{_esc(job['niche'])}</b> in <b>{_esc(job['location'])}</b> "
         f"&nbsp;·&nbsp; job id <code>{_esc(job['id'][:8])}</code></div>"]

    h.append("<div class='kpis'>")
    for n, l in ((f"{passed}/{total}", "checks passed"),
                 (str(job["total_scraped"]), "candidates scraped"),
                 (str(job["total_verified"]), "leads verified"),
                 (str(live.get("exported") if live.get("exported") is not None else len(leads)),
                  "leads delivered"),
                 (str(len(leads)), "leads in job"),
                 (timings.get("runtime", "—"), "pipeline runtime")):
        h.append(f"<div class='kpi'><div class='n'>{_esc(n)}</div><div class='l'>{_esc(l)}</div></div>")
    h.append("</div>")

    failed = [r for r in RESULTS if not r["passed"]]
    h.append(f"<div class='note'><b>Result:</b> {passed} of {total} checks passed"
             + ("." if not failed else f" — <span class='fail'>{len(failed)} FAILED</span>: "
                + _esc(", ".join(r["name"] for r in failed)))
             + f"<br><b>Unit suite:</b> {_esc(suite_line)}</div>")

    for g, rows in groups.items():
        gp = sum(1 for r in rows if r["passed"])
        h.append(f"<h2>{_esc(g)} &nbsp;<span class='det'>({gp}/{len(rows)})</span></h2>")
        h.append("<table><tr><th style='width:58px'>Result</th><th>Check</th>"
                 "<th style='width:33%'>Detail</th></tr>")
        for r in rows:
            cls = "pass" if r["passed"] else "fail"
            txt = "PASS" if r["passed"] else "FAIL"
            ev = f"<div class='ev'>{_esc(r['evidence'])}</div>" if r["evidence"] else ""
            h.append(f"<tr><td class='st {cls}'>{txt}</td><td>{_esc(r['name'])}</td>"
                     f"<td class='det'>{_esc(r['detail'])}{ev}</td></tr>")
        h.append("</table>")

    h.append("<h2>Leads delivered by this run</h2>")
    for l in sorted(leads, key=lambda x: -(x.get("lead_score") or 0)):
        h.append("<div class='lead'>")
        h.append(f"<div class='nm'>{_esc(l['name'])}</div>")
        h.append(f"<div class='meta'>score <b>{l.get('lead_score')}</b> &nbsp;·&nbsp; "
                 f"confidence {l.get('data_confidence')} &nbsp;·&nbsp; "
                 f"{_esc(l.get('verification_tier'))} &nbsp;·&nbsp; "
                 f"{_esc(l.get('business_category') or '')}</div>")
        for k, v in (("Phone", l.get("phone_formatted") or l.get("phone")),
                     ("Email", l.get("owner_email")), ("Owner", l.get("owner_name")),
                     ("Website", l.get("website")),
                     ("Rating", f"{l.get('google_rating')} ({l.get('google_review_count')} reviews)"
                      if l.get("google_rating") else None)):
            if v:
                h.append(f"<div class='row'><span class='k'>{k}</span>{_esc(v)}</div>")
        if l.get("pitch_angle"):
            h.append(f"<div class='row'><span class='k'>Pitch</span>{_esc(l['pitch_angle'])}</div>")
        if l.get("opener"):
            h.append(f"<div class='row'><span class='k'>Opener</span>{_esc(l['opener'])}</div>")
        if l.get("pain_points"):
            h.append(f"<div class='row'><span class='k'>Pain points</span>{_esc(l['pain_points'])}</div>")
        h.append("</div>")

    h.append("<div class='foot'>Generated by scripts/e2e_lifecycle_test.py against the "
             "running stack (FastAPI + Celery + Redis). Every check exercises real code; "
             "the live pipeline section reflects an actual scrape, enrich, score, LLM and "
             "export cycle.</div>")

    html = "".join(h)
    from backend.services.pdf_builder import _render_chromium_sync
    if _render_chromium_sync(html, out_path):
        return out_path
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(str(out_path))
        return out_path
    except Exception:
        out_path.with_suffix(".html").write_text(html)
        return out_path.with_suffix(".html")


def main():
    job_id = sys.argv[1]
    print("\n=== Lifecycle checks ===", flush=True)
    test_broker_outage_is_reported()
    test_stale_job_reconciliation()
    test_acks_late_config()
    test_resume_skips_completed_work()
    test_timeout_handler_exists()
    test_delete_removes_exports_but_keeps_delivery_record()
    test_cross_run_dedup_excludes_delivered()
    test_data_quality_gates()
    test_circuit_isolation()
    test_api_surface()
    print("\n=== Live job ===", flush=True)
    live = verify_live_job(job_id)

    suite = subprocess.run([".venv/bin/python", "-m", "pytest", "tests", "backend/tests",
                            "-q", "--no-header", "-p", "no:cacheprovider"],
                           capture_output=True, text=True)
    _ansi = __import__("re").compile(r"\x1b\[[0-9;]*m")
    _out = _ansi.sub("", suite.stdout)
    suite_line = [l for l in _out.strip().splitlines() if "passed" in l or "failed" in l]
    suite_line = suite_line[-1].strip() if suite_line else "not run"

    runtime = "—"
    try:
        import re as _re
        log = open("/tmp/e2e-celery.log").read()
        m = _re.findall(r"succeeded in ([0-9.]+)s", log)
        if m: runtime = f"{float(m[-1])/60:.1f} min"
    except Exception:
        pass

    out = Path("exports") / f"E2E_Lifecycle_Report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    p = build_report(live, out, suite_line, {"runtime": runtime})
    total = len(RESULTS); passed = sum(1 for r in RESULTS if r["passed"])
    print(f"\n{'='*60}\n{passed}/{total} checks passed | suite: {suite_line}\nreport: {p}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

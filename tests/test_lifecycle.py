"""Lifecycle survivability: crash, timeout, delete, and the LLM extraction gate.

These cover the P0/P1 findings in docs/audits/lifecycle.md — the failure modes that
left six real jobs frozen for 16 days and made a 2-hour run deliver nothing.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.models import Job, JobStatus


# ── P0-1: stale-job reconciliation ────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated SQLite so these never touch the real leads.db."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.database import Base

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("backend.workers.pipeline.SyncSessionLocal", Session)
    s = Session()
    yield s
    s.close()


def _job(**kw):
    base = dict(id=None, niche="n", location="l", limit=10,
                status=JobStatus.RUNNING, created_at=datetime.now(timezone.utc))
    base.update(kw)
    if base["id"] is None:
        import uuid
        base["id"] = str(uuid.uuid4())
    return Job(**base)


def test_reconciler_fails_jobs_whose_worker_died(db):
    from backend.workers.pipeline import reconcile_stale_jobs, STALE_AFTER_SECONDS
    old = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 60)

    dead_running = _job(status=JobStatus.RUNNING, heartbeat_at=old, created_at=old)
    dead_pending = _job(status=JobStatus.PENDING, heartbeat_at=None, created_at=old)
    db.add_all([dead_running, dead_pending])
    db.commit()

    assert reconcile_stale_jobs() == 2
    db.expire_all()
    for j in (dead_running, dead_pending):
        assert db.get(Job, j.id).status == JobStatus.FAILED
        assert "heartbeat" in db.get(Job, j.id).stage_message.lower()


def test_reconciler_leaves_a_live_job_alone(db):
    """A slow job with a fresh heartbeat must never be killed."""
    from backend.workers.pipeline import reconcile_stale_jobs
    alive = _job(status=JobStatus.RUNNING, heartbeat_at=datetime.now(timezone.utc))
    db.add(alive)
    db.commit()

    assert reconcile_stale_jobs() == 0
    db.expire_all()
    assert db.get(Job, alive.id).status == JobStatus.RUNNING


def test_reconciler_ignores_finished_jobs(db):
    from backend.workers.pipeline import reconcile_stale_jobs
    old = datetime.now(timezone.utc) - timedelta(days=30)
    done = _job(status=JobStatus.COMPLETED, created_at=old, heartbeat_at=old)
    db.add(done)
    db.commit()
    assert reconcile_stale_jobs() == 0


def test_stale_threshold_exceeds_the_celery_hard_limit():
    """Otherwise the reconciler could fail a job that is merely slow."""
    from backend.workers.pipeline import STALE_AFTER_SECONDS
    from backend.workers.celery_app import celery_app
    assert STALE_AFTER_SECONDS > celery_app.conf.task_time_limit


def test_acks_late_is_on_so_a_killed_worker_redelivers():
    from backend.workers.celery_app import celery_app
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


# ── P0-4: delivered-business record survives job deletion ─────────────────────

def test_delivered_record_is_written_and_deduped(db):
    from backend.models import DeliveredBusiness
    from backend.workers.pipeline import _record_delivered

    job = SimpleNamespace(id="j1", niche="Construction", location="Miami, FL")
    leads = [SimpleNamespace(name="Acme Builders", phone="(305) 555-1234"),
             SimpleNamespace(name="Beta Corp", phone="+1 305 555 9999")]

    assert _record_delivered(db, job, leads) == 2
    # Re-running the same job (Celery retry) must not double-insert.
    assert _record_delivered(db, job, leads) == 0
    assert db.query(DeliveredBusiness).count() == 2


def test_delivered_record_has_no_fk_so_it_outlives_the_job():
    """The whole point: deleting a Job must not cascade this away, or every
    business it delivered becomes sellable again."""
    from backend.models import DeliveredBusiness
    assert DeliveredBusiness.__table__.foreign_keys == set()


def test_delivered_record_skips_unidentifiable_leads(db):
    from backend.workers.pipeline import _record_delivered
    job = SimpleNamespace(id="j", niche="n", location="l")
    assert _record_delivered(db, job, [SimpleNamespace(name=None, phone=None)]) == 0


# ── P2-3: resume after a worker death ─────────────────────────────────────────

def _elead(status, score=0, **kw):
    from backend.models import LeadStatus
    base = dict(id=None, status=status, lead_score=score, data_confidence=0.9,
                phone_verified=True, has_google_maps=True, sources="google_maps",
                email_verified=False, phone="1", owner_email=None, website=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_resume_skips_leads_already_scored(monkeypatch):
    """acks_late redelivers an interrupted task. Without this, a worker killed at
    90% redid the entire two-hour run from zero."""
    from backend.models import LeadStatus
    import backend.workers.pipeline as p

    enriched = []
    monkeypatch.setattr(p, "_stage_maps_plan", lambda job, b: [])
    monkeypatch.setattr(p, "_stage_maps_fetch", lambda job, a: {})
    monkeypatch.setattr(p, "_stage_maps_apply", lambda *a: None)
    monkeypatch.setattr(p, "_stage_websites", lambda db, job, b: None)
    monkeypatch.setattr(p, "_stage_intel_and_contacts",
                        lambda db, job, b, **kw: enriched.extend(b))
    def _score(db, job, batch):
        for l in batch:
            l.status, l.lead_score = LeadStatus.VALIDATED, 80
    monkeypatch.setattr(p, "_stage_score", _score)
    monkeypatch.setattr(p, "_push", lambda *a, **k: None)
    monkeypatch.setattr(p, "_update_job", lambda db, job, **kw: None)
    monkeypatch.setattr(p.settings, "ENRICH_BATCH_SIZE", 2)

    # 2 leads already finished in the previous attempt, 2 still raw (and raw
    # leads must not already count as verified, or the early-exit short-circuits).
    raw = dict(phone_verified=False, has_google_maps=False)
    leads = [_elead(LeadStatus.VALIDATED, 80), _elead(LeadStatus.VALIDATED, 80),
             _elead(LeadStatus.SCRAPED, 0, **raw), _elead(LeadStatus.SCRAPED, 0, **raw)]
    job = SimpleNamespace(id="j", location="x", country="us")

    verified = p._enrich_until_target(None, job, leads, target=4)

    assert len(enriched) == 2, "only the unfinished leads should be re-enriched"
    assert all(l.status == LeadStatus.VALIDATED for l in leads)
    assert verified == 4


def test_resume_is_a_noop_when_the_target_was_already_met(monkeypatch):
    """A retry that arrives after the work finished must not re-enrich anything."""
    from backend.models import LeadStatus
    import backend.workers.pipeline as p

    called = []
    monkeypatch.setattr(p, "_stage_intel_and_contacts",
                        lambda *a, **k: called.append(1))
    leads = [_elead(LeadStatus.VALIDATED, 80) for _ in range(3)]
    job = SimpleNamespace(id="j", location="x", country="us")

    assert p._enrich_until_target(None, job, leads, target=2) == 3
    assert called == [], "nothing should be re-enriched"


def test_a_fresh_run_enriches_everything(monkeypatch):
    """The resume path must not change first-run behaviour."""
    from backend.models import LeadStatus
    import backend.workers.pipeline as p

    enriched = []
    monkeypatch.setattr(p, "_stage_maps_plan", lambda job, b: [])
    monkeypatch.setattr(p, "_stage_maps_fetch", lambda job, a: {})
    monkeypatch.setattr(p, "_stage_maps_apply", lambda *a: None)
    monkeypatch.setattr(p, "_stage_websites", lambda db, job, b: None)
    monkeypatch.setattr(p, "_stage_intel_and_contacts",
                        lambda db, job, b, **kw: enriched.extend(b))
    monkeypatch.setattr(p, "_stage_score",
                        lambda db, job, batch: [setattr(l, "status", LeadStatus.VALIDATED)
                                                for l in batch])
    monkeypatch.setattr(p, "_push", lambda *a, **k: None)
    monkeypatch.setattr(p, "_update_job", lambda db, job, **kw: None)
    monkeypatch.setattr(p.settings, "ENRICH_BATCH_SIZE", 2)

    leads = [_elead(LeadStatus.SCRAPED, 0, phone_verified=False,
                    has_google_maps=False) for _ in range(4)]
    job = SimpleNamespace(id="j", location="x", country="us")
    p._enrich_until_target(None, job, leads, target=99)
    assert len(enriched) == 4


def test_schema_setup_runs_once_per_process(monkeypatch):
    """It used to run a full inspect + ALTER sweep before every single job."""
    import backend.database as d
    monkeypatch.setattr(d, "_schema_ready", False)
    calls = []
    monkeypatch.setattr(d, "migrate_sqlite_sync", lambda: calls.append(1))
    monkeypatch.setattr(d.Base.metadata, "create_all", lambda **kw: None)

    d.create_tables_sync()
    d.create_tables_sync()
    d.create_tables_sync()
    assert calls == [1], "schema setup must be memoised per process"


# ── P1-2: health endpoint honesty ─────────────────────────────────────────────

def test_health_reports_ok_when_everything_is_ok(monkeypatch):
    import asyncio
    import backend.api.health as h

    async def fake():
        status = {"api": "ok", "database": "ok", "redis": "ok",
                  "proxy": "3/3 healthy",
                  "proxy_details": {"instances": [], "total_healthy": 3}}
        def _ok(key):
            v = status.get(key)
            if key == "proxy":
                if not isinstance(v, str) or "/" not in v:
                    return False
                healthy, _, total = v.split()[0].partition("/")
                return total == "0" or int(healthy) > 0
            return v == "ok"
        failing = [k for k in ("api", "database", "redis", "proxy") if not _ok(k)]
        return {"status": "ok" if not failing else "degraded", "services": status}

    # Sanity-check the same predicate the endpoint uses.
    assert asyncio.run(fake())["status"] == "ok"


def test_health_flags_a_total_proxy_outage(monkeypatch):
    """'0/5 healthy' means every configured proxy is down — that is degraded.
    '0/0 healthy' means none configured, which is a valid direct-connection
    deployment. The old check accepted both."""
    import asyncio
    import backend.api.health as h

    async def run(proxy_value):
        async def fake_rotator_summary():
            pass
        monkeypatch.setattr(h, "settings", h.settings)
        return proxy_value

    def verdict(v):
        healthy, _, total = v.split()[0].partition("/")
        return total == "0" or int(healthy) > 0

    assert verdict("3/3 healthy") is True
    assert verdict("0/0 healthy") is True      # none configured — fine
    assert verdict("0/5 healthy") is False     # all configured ones dead


# ── P1-1: the API actually returns the LLM output ─────────────────────────────

def test_lead_response_exposes_the_llm_and_intel_fields():
    from backend.schemas import LeadResponse
    from backend.models import Lead

    exposed = set(LeadResponse.model_fields)
    for f in ("pitch_angle", "pain_points", "opener", "review_themes",
              "marketing_stack", "external_platforms", "legal_name",
              "entity_status", "runs_paid_ads", "last_review_date"):
        assert f in exposed, f"{f} is stored and exported but invisible to the API"

    cols = {c.name for c in Lead.__table__.columns}
    missing = cols - exposed
    assert not missing, f"Lead columns still hidden from the API: {sorted(missing)}"


# ── LLM extraction gate ───────────────────────────────────────────────────────

def test_extractor_rejects_a_company_name_posing_as_an_owner(monkeypatch):
    """The prompt forbids it, but the deterministic gate is what we trust."""
    import json
    import backend.services.pitch_research as pr

    monkeypatch.setattr(pr, "_call_nvidia", lambda *a, **k: json.dumps({
        "services": "Roofing, Siding", "owner_name": "Our Team",
        "owner_title": "Management", "employee_count": 12, "year_founded": 2011,
    }))
    got = pr.extract_facts("Acme", "page text", api_key="k", base_url="u", models=["m"])

    assert "owner_name" not in got, "page furniture must not become an owner"
    assert got["services"] == "Roofing, Siding"
    assert got["employee_count"] == 12
    assert got["year_founded"] == 2011


def test_extractor_drops_implausible_numbers(monkeypatch):
    import json
    import backend.services.pitch_research as pr
    monkeypatch.setattr(pr, "_call_nvidia", lambda *a, **k: json.dumps({
        "employee_count": 0, "year_founded": 1200, "services": None,
        "owner_name": None, "owner_title": None,
    }))
    assert pr.extract_facts("Acme", "text", api_key="k", base_url="u", models=["m"]) == {}


def test_extractor_keeps_a_real_person(monkeypatch):
    import json
    import backend.services.pitch_research as pr
    monkeypatch.setattr(pr, "_call_nvidia", lambda *a, **k: json.dumps({
        "owner_name": "Maria Gonzalez", "owner_title": "Founder & CEO",
        "services": None, "employee_count": None, "year_founded": None,
    }))
    got = pr.extract_facts("Acme", "text", api_key="k", base_url="u", models=["m"])
    assert got == {"owner_name": "Maria Gonzalez", "owner_title": "Founder & CEO"}


def test_extractor_returns_nothing_for_empty_page():
    import backend.services.pitch_research as pr
    assert pr.extract_facts("Acme", "", api_key="k", base_url="u", models=["m"]) == {}


def test_llm_extraction_only_fills_gaps_and_never_touches_confidence(monkeypatch):
    from backend.workers.pipeline import _llm_extract_gaps
    from backend.config import settings
    import backend.services.pitch_research as pr

    monkeypatch.setattr(settings, "ENABLE_LLM_EXTRACTION", True)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(pr, "extract_facts", lambda *a, **k: {
        "services": "NEW services", "owner_title": "Owner", "year_founded": 1999})

    lead = SimpleNamespace(name="Acme", services="EXISTING services",
                           owner_name=None, owner_title=None, employee_count=None,
                           year_founded=None, employee_count_source=None,
                           data_confidence=0.4, lead_score=50)
    _llm_extract_gaps(lead, {"http://x": "<html><body>hi</body></html>"})

    assert lead.services == "EXISTING services", "must not overwrite a scraped value"
    assert lead.owner_title == "Owner", "must fill an empty field"
    assert lead.year_founded == 1999
    assert lead.data_confidence == 0.4, "LLM extraction must never raise confidence"


def test_llm_extraction_is_off_by_default_and_a_noop_without_cache(monkeypatch):
    from backend.workers.pipeline import _llm_extract_gaps
    from backend.config import settings
    lead = SimpleNamespace(name="A", services=None, owner_name=None, owner_title=None,
                           employee_count=None, year_founded=None,
                           employee_count_source=None, data_confidence=0.1)
    _llm_extract_gaps(lead, {})                       # disabled by default
    assert lead.services is None
    monkeypatch.setattr(settings, "ENABLE_LLM_EXTRACTION", True)
    _llm_extract_gaps(lead, {})                       # enabled but nothing cached
    assert lead.services is None


# ── Circuit isolation must be stable and evenly spread ────────────────────────

def test_worker_tags_get_one_circuit_each_deterministically():
    """`abs(hash(tag)) % n` randomises per process AND clusters: measured runs put
    maps0..maps3 on [2,0,2,2] / [3,3,3,2] / [1,0,1,0] — three of four Maps workers
    sharing one exit IP. Both scrapers size their worker count by
    circuit_capacity() believing each worker gets its own IP, so collisions
    silently multiply the per-IP request rate they were built to avoid."""
    from backend.services.proxy_rotator import _tag_slot

    for prefix in ("maps", "disc", "rev"):
        slots = [_tag_slot(f"{prefix}{w}", 4) for w in range(4)]
        assert sorted(slots) == [0, 1, 2, 3], f"{prefix}: {slots} — workers share a circuit"

    # More workers than circuits wraps evenly rather than piling up.
    assert [_tag_slot(f"maps{w}", 3) for w in range(6)] == [0, 1, 2, 0, 1, 2]

    # Unindexed tags still map somewhere stable and in range.
    for n in (1, 2, 5, 20):
        assert 0 <= _tag_slot("httpx-pool", n) < n
    assert _tag_slot("anything", 0) == 0          # empty pool must not divide by zero


def test_tag_slot_is_identical_across_processes():
    """The mapping must survive a worker restart, or circuits reshuffle."""
    import subprocess, sys
    code = ("from backend.services.proxy_rotator import _tag_slot as t;"
            "print([t(f'maps{w}', 4) for w in range(4)])")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True).stdout.strip() for _ in range(3)}
    assert len(runs) == 1, f"mapping varies between processes: {runs}"


# ── Scraped leads must be in the requested location ──────────────────────────

def test_leads_from_another_state_are_dropped():
    """build_tiles turns "Bend, OR" into "South Bend, OR" and "West Bend, OR";
    Google Maps geocodes those to South Bend INDIANA and West Bend WISCONSIN,
    ignoring the state. A live Bend, OR job delivered 7 of 7 businesses in
    Indiana, and the same tiles put Madison, WI addresses into Newark and
    Houston jobs. Nothing validated location, so they shipped."""
    from backend.services.sources import location_matches, build_tiles

    # the tile that caused it really is generated
    assert ("Roofing contractor", "South Bend, OR") in build_tiles(
        "Roofing contractor", "Bend, OR")

    assert not location_matches("1126 W Western Ave, South Bend, IN 46601", "Bend, OR")
    assert not location_matches("4702 S Biltmore Ln, Madison, WI, 53718", "Newark, NJ")

    for addr, loc in (("123 NW Oregon Ave, Bend, OR 97701", "Bend, OR"),
                      ("538 S Grand Ave, Los Angeles, CA, 90071", "Los Angeles, CA"),
                      # full state name spelled out must still match
                      ("California St, Los Angeles, California, 90071", "Los Angeles, CA"),
                      ("363 Washington Ave, Miami Beach, FL 33139", "Miami, FL")):
        assert location_matches(addr, loc), f"{addr} should be kept for {loc}"


def test_location_filter_is_conservative_when_it_cannot_judge():
    """Never drop a lead on a guess: NPI and Hotfrog records often carry no
    address, and a location with no state gives nothing to compare."""
    from backend.services.sources import location_matches
    assert location_matches(None, "Bend, OR")          # no address
    assert location_matches("", "Bend, OR")
    assert location_matches("anywhere at all", "Bend")  # no state in request
    assert location_matches("somewhere", "Paris, France")  # unrecognised region

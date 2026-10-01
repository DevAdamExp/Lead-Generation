"""Re-running _stage_scrape for the same job must NOT duplicate leads (Celery
retry / mid-scrape crash idempotency)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.services.sources as sources
from backend.models import Base, Job, Lead
from backend.workers.pipeline import _stage_scrape

_FAKE = [
    {"name": "Acme Plumbing", "phone": "+1 415 555 0100", "sources_str": "google_maps"},
    {"name": "Best Pipes", "phone": "+1 415 555 0101", "sources_str": "google_maps"},
    {"name": "City Drains", "phone": "+1 415 555 0102", "sources_str": "yelp"},
]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_rerun_does_not_duplicate(db, monkeypatch):
    monkeypatch.setattr(sources, "gather_leads_tiled", lambda **kw: list(_FAKE))
    job = Job(id="job-1", niche="plumber", location="SF", country="us", limit=5)
    db.add(job)
    db.commit()

    first = _stage_scrape(db, job, pool_target=5)
    assert len(first) == 3

    # Simulate a Celery retry: same job, scrape runs again.
    second = _stage_scrape(db, job, pool_target=5)
    assert len(second) == 3, "re-run must rebuild, not double-insert"
    assert db.query(Lead).filter(Lead.job_id == "job-1").count() == 3

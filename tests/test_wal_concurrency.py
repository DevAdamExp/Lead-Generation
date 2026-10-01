"""
tests/test_wal_concurrency.py — Verify SQLite WAL mode allows concurrent writes.
"""
import pytest
import threading
import time
from pathlib import Path
from sqlalchemy import text
from backend.database import SyncSessionLocal, create_tables_sync
from backend.config import settings


@pytest.fixture(autouse=True, scope="module")
def _schema():
    """A fresh clone has no leads.db yet; the app creates tables on startup."""
    create_tables_sync()


def test_wal_mode_enabled():
    """PRAGMA journal_mode should return 'wal'."""
    with SyncSessionLocal() as session:
        result = session.execute(
            text("PRAGMA journal_mode")
        ).scalar()
        assert result.lower() == "wal", f"Expected WAL, got {result}"


def test_busy_timeout_set():
    """PRAGMA busy_timeout should be 5000."""
    with SyncSessionLocal() as session:
        result = session.execute(
            text("PRAGMA busy_timeout")
        ).scalar()
        assert result >= 5000, f"Expected >=5000, got {result}"


def test_concurrent_writes():
    """
    Launch 10 threads that each INSERT a row and commit.
    Under WAL mode all should succeed without 'database is locked' errors.
    """
    from backend.models import Job

    errors = []
    lock = threading.Lock()

    def _write(idx: int):
        try:
            job_id = f"wal-test-{idx}-{time.time()}"
            with SyncSessionLocal() as session:
                job = Job(
                    id=job_id,
                    niche=f"WAL Test {idx}",
                    location="TestLocation",
                    country="us",
                    limit=10,
                )
                session.add(job)
                session.commit()
        except Exception as e:
            with lock:
                errors.append(f"Thread {idx}: {e}")

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    # Cleanup: remove test jobs
    with SyncSessionLocal() as session:
        session.execute(
            text("DELETE FROM jobs WHERE id LIKE 'wal-test-%'")
        )
        session.commit()

    assert len(errors) == 0, f"Concurrent write errors: {errors}"

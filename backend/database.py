"""
database.py — SQLAlchemy engines and session factories.
Two engines: async (FastAPI) + sync (Celery workers).
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from backend.config import settings

# ── SQLite WAL mode + busy timeout for write-concurrency safety ──
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

# Async engine for FastAPI
async_engine = create_async_engine(
    settings.DATABASE_URL, echo=settings.DEBUG,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

# Attach WAL pragma to both engines if SQLite
if "sqlite" in settings.DATABASE_URL:
    event.listen(async_engine.sync_engine, "connect", _set_sqlite_pragma)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine, class_=AsyncSession,
    expire_on_commit=False, autocommit=False, autoflush=False,
)

async def get_async_db():
    async with AsyncSessionLocal() as session:
        yield session

# Sync engine for Celery
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.SYNC_DATABASE_URL else {},
)
if "sqlite" in settings.SYNC_DATABASE_URL:
    event.listen(sync_engine, "connect", _set_sqlite_pragma)
SyncSessionLocal = sessionmaker(bind=sync_engine, autocommit=False, autoflush=False)

class Base(DeclarativeBase):
    pass

async def create_tables():
    async with async_engine.begin() as conn:
        from backend import models  # noqa
        await conn.run_sync(Base.metadata.create_all)

_schema_ready = False


def create_tables_sync(force: bool = False):
    """Create tables + apply additive migrations.

    Memoised per process: this used to run on EVERY Celery task, meaning a full
    `inspect()`, an ALTER TABLE attempt per missing column and a CREATE INDEX
    sweep before each job. The schema cannot change mid-process, so once is
    enough. Pass force=True from a migration script if you really need a re-check.
    """
    global _schema_ready
    if _schema_ready and not force:
        return
    from backend import models  # noqa
    Base.metadata.create_all(bind=sync_engine)
    migrate_sqlite_sync()
    _schema_ready = True


def migrate_sqlite_sync():
    """
    Lightweight additive migration for SQLite: add any model columns that are
    missing from an existing table. Avoids a full migration framework for the
    simple 'new columns only' changes this project makes.
    """
    if "sqlite" not in settings.SYNC_DATABASE_URL:
        return
    from sqlalchemy import inspect, text
    from backend import models  # noqa

    insp = inspect(sync_engine)
    type_map = {
        "INTEGER": "INTEGER", "BIGINT": "INTEGER", "SMALLINT": "INTEGER",
        "FLOAT": "REAL", "REAL": "REAL", "NUMERIC": "REAL",
        "BOOLEAN": "BOOLEAN",
        "VARCHAR": "VARCHAR", "TEXT": "TEXT", "STRING": "VARCHAR",
        "DATETIME": "DATETIME",
    }
    with sync_engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                col_type = type(col.type).__name__.upper()
                sql_type = type_map.get(col_type, "VARCHAR")
                try:
                    conn.execute(text(
                        f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {sql_type}'
                    ))
                except Exception:
                    pass

        # Cross-run dedup (pipeline._load_delivered_exclude) scans every Lead row
        # across every prior job by phone/name on each new job. metadata.create_all
        # skips existing tables, so declaring index=True on the model would never
        # reach an existing leads.db — create them here instead.
        for stmt in (
            "CREATE INDEX IF NOT EXISTS ix_leads_phone ON leads (phone)",
            "CREATE INDEX IF NOT EXISTS ix_leads_name ON leads (name)",
            "CREATE INDEX IF NOT EXISTS ix_leads_job_id ON leads (job_id)",
        ):
            try:
                conn.execute(text(stmt))
            except Exception:
                pass

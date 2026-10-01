"""
main.py — FastAPI application entrypoint.
Fixes: #9 (exports dir creation), #19 (logging), #20 (health check registered).
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path

from backend.config import settings
from backend.database import create_tables
from backend.utils.logger import setup_logging
from backend.api import jobs, leads, ws, health

setup_logging(debug=settings.DEBUG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Lead-Generator API v%s", settings.APP_VERSION)
    await create_tables()
    logger.info("Database tables ready")

    # A worker killed mid-run leaves its Job row RUNNING forever — nothing else
    # reconciles it, and the UI polls such a job indefinitely. Sweep on startup.
    try:
        import asyncio
        from backend.workers.pipeline import reconcile_stale_jobs
        n = await asyncio.to_thread(reconcile_stale_jobs)
        if n:
            logger.warning("Marked %d stale job(s) as failed on startup", n)
    except Exception as e:
        logger.warning("Startup job reconciliation skipped: %s", e)

    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# allow_credentials=True with allow_origins=["*"] is rejected outright by every
# browser (the spec forbids the pair), so the credentialed path was silently
# dead. Nothing here uses cookies or auth headers — drop credentials and keep the
# wildcard honest.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(health.router, prefix="/api")
app.include_router(jobs.router,   prefix="/api", tags=["Jobs"])
app.include_router(leads.router,  prefix="/api", tags=["Leads"])
app.include_router(ws.router,     tags=["WebSocket"])

# Bug #9 fix — ensure exports dir exists BEFORE mounting
exports_dir = settings.EXPORTS_DIR
exports_dir.mkdir(parents=True, exist_ok=True)
app.mount("/exports", StaticFiles(directory=str(exports_dir)), name="exports")

# Frontend static files — only a BUILT export, never the Next.js source tree.
# This used to mount frontend/ itself, publicly serving package.json, tsconfig,
# and all of src/. In dev the UI is served by `npm run dev` on :3000 instead.
frontend_dist = Path(__file__).parent.parent / "frontend" / "out"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    logger.info("Frontend served from %s", frontend_dist)
else:
    logger.info("No built frontend at %s — run `npm run build` in frontend/ "
                "or use the Next dev server on :3000", frontend_dist)

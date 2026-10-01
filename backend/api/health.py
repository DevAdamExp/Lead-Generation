"""
api/health.py — Health check endpoint (Bug #20 fix).
"""
from fastapi import APIRouter
from sqlalchemy import text
from backend.database import async_engine
from backend.config import settings

router = APIRouter()


@router.get("/health", tags=["System"])
async def health_check():
    status = {"api": "ok", "database": "unknown", "redis": "unknown", "proxy": "unknown"}

    # Database check
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["database"] = "ok"
    except Exception as e:
        status["database"] = f"error: {str(e)[:80]}"

    # Redis check
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"unavailable: {str(e)[:80]}"

    # Proxy check
    try:
        from backend.services.proxy_rotator import get_rotator
        rotator = get_rotator()
        summary = rotator.health_summary()
        healthy = summary["total_healthy"]
        total = len(summary["instances"]) + summary["external_proxies"]
        status["proxy"] = f"{healthy}/{total} healthy"
        status["proxy_details"] = summary
    except Exception as e:
        status["proxy"] = f"not configured: {str(e)[:80]}"

    # Judge only the service verdicts — `proxy_details` is a dict and used to be
    # swept into this check, which made `all()` False on every call so the
    # endpoint reported "degraded" even when every service was ok. A proxy value
    # of "0/0 healthy" also used to pass, hiding a total proxy outage.
    def _ok(key: str) -> bool:
        v = status.get(key)
        if key == "proxy":
            if not isinstance(v, str) or "/" not in v:
                return False
            healthy, _, total = v.split()[0].partition("/")
            # No proxies configured at all is a valid deployment (direct
            # connection); some configured but none healthy is not.
            return total == "0" or int(healthy) > 0
        return v == "ok"

    checked = ("api", "database", "redis", "proxy")
    failing = [k for k in checked if not _ok(k)]
    status["failing"] = failing
    return {"status": "ok" if not failing else "degraded", "services": status}

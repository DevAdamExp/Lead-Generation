"""
api/ws.py — WebSocket endpoint for live progress streaming.
Bug #8 fix: Redis errors are caught and a fallback polling signal is sent.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Slightly beyond Celery's hard time limit — past this a job cannot still be alive.
_MAX_WATCH_SECONDS = 8100


@router.websocket("/ws/jobs/{job_id}")
async def job_progress_ws(websocket: WebSocket, job_id: str):
    await websocket.accept()

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True,
                               socket_connect_timeout=3)
        redis_ok = True
    except Exception:
        redis_ok = False
        r = None

    if not redis_ok:
        # Tell client to fall back to HTTP polling
        await websocket.send_text(json.dumps({
            "type": "fallback_poll",
            "job_id": job_id,
            "message": "Redis unavailable — use HTTP polling on /api/jobs/{job_id}",
            "progress_pct": 0,
            "stage": "",
            "stats": {},
        }))
        await websocket.close()
        return

    try:
        last_pct = -1
        consecutive_errors = 0
        # Bounded: this loop used to be `while True` at 0.5s with no ceiling, so a
        # job whose worker died (never writing complete/error) pinned one server
        # connection per abandoned browser tab, forever. Stop a little past the
        # Celery hard limit and tell the client to give up.
        deadline = asyncio.get_running_loop().time() + _MAX_WATCH_SECONDS

        while True:
            if asyncio.get_running_loop().time() > deadline:
                try:
                    await websocket.send_text(json.dumps({
                        "type": "error", "job_id": job_id,
                        "message": ("Stopped watching this job — it exceeded the maximum "
                                    "run time. Refresh to check its final status."),
                        "progress_pct": 0, "stage": "Timeout", "stats": {},
                    }))
                except Exception:
                    pass
                break

            try:
                data = await r.hgetall(f"job:{job_id}:progress")
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.warning("WS Redis read error (%d): %s", consecutive_errors, e)
                if consecutive_errors >= 5:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "job_id": job_id,
                        "message": "Lost connection to progress stream. Please refresh.",
                        "progress_pct": 0,
                        "stage": "Error",
                        "stats": {},
                    }))
                    break
                await asyncio.sleep(2)
                continue

            if data:
                pct = int(data.get("progress_pct", 0))
                msg_type = data.get("type", "progress")

                if pct != last_pct or msg_type in ("complete", "error"):
                    last_pct = pct
                    try:
                        await websocket.send_text(json.dumps({
                            "type": msg_type,
                            "job_id": job_id,
                            "progress_pct": pct,
                            "stage": data.get("stage", ""),
                            "message": data.get("message", ""),
                            "stats": {
                                "total_scraped":  int(data.get("total_scraped", 0)),
                                "total_verified": int(data.get("total_verified", 0)),
                            },
                        }))
                    except Exception:
                        break  # client disconnected

                    if msg_type in ("complete", "error"):
                        break

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("WebSocket error for job %s: %s", job_id, e)
    finally:
        if r:
            await r.aclose()

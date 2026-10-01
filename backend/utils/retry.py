"""
utils/retry.py — Retry logic with exponential backoff for all HTTP fetches.

Provides:
  - retry_async: for async functions (Playwright, httpx async)
  - retry_sync: for sync functions (httpx sync, Celery tasks)
  - RetryableError: raise this to trigger a retry (non-fatal)
  - FatalError: raise this to abort (no retry)

All scrapers and fetchers should use these instead of raw try/except.
"""
import asyncio
import logging
import random
import time
from functools import wraps
from typing import Type, Tuple, Callable, Any

logger = logging.getLogger(__name__)


class RetryableError(Exception):
    """Raise this to trigger a retry (transient failure — network, rate-limit, etc.)."""


class FatalError(Exception):
    """Raise this to abort immediately (permanent failure — bad data, auth, etc.)."""


def exponential_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> float:
    """Calculate delay with exponential backoff + optional jitter."""
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    if jitter:
        delay *= 1 + random.random() * 0.5  # 1x - 1.5x jitter
    return delay


def retry_sync(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (
        RetryableError,
        ConnectionError,
        TimeoutError,
        OSError,
    ),
    logger_name: str = None,
):
    """
    Decorator: retry a sync function with exponential backoff.

    Usage:
        @retry_sync(max_attempts=3)
        def fetch_data(url: str) -> dict:
            ...

        @retry_sync(max_attempts=5, base_delay=2.0)
        def scrape_page(url: str) -> str:
            ...
    """
    log = logging.getLogger(logger_name or __name__)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except FatalError:
                    raise
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        delay = exponential_backoff(attempt, base_delay, max_delay)
                        log.warning(
                            "%s attempt %d/%d failed: %s. Retrying in %.1fs…",
                            func.__name__, attempt, max_attempts, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        log.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_attempts, e,
                        )
                except Exception as e:
                    # Unexpected exception — log and re-raise immediately
                    log.error(
                        "%s unexpected error on attempt %d/%d: %s",
                        func.__name__, attempt, max_attempts, e,
                    )
                    raise
            raise last_exc or RuntimeError(f"{func.__name__} failed (unknown)")
        return wrapper
    return decorator


def retry_async(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (
        RetryableError,
        ConnectionError,
        TimeoutError,
        OSError,
    ),
    logger_name: str = None,
):
    """
    Decorator: retry an async function with exponential backoff.

    Usage:
        @retry_async(max_attempts=3)
        async def fetch_page(url: str) -> str:
            ...
    """
    log = logging.getLogger(logger_name or __name__)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except FatalError:
                    raise
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        delay = exponential_backoff(attempt, base_delay, max_delay)
                        log.warning(
                            "%s attempt %d/%d failed: %s. Retrying in %.1fs…",
                            func.__name__, attempt, max_attempts, e, delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_attempts, e,
                        )
                except Exception as e:
                    log.error(
                        "%s unexpected error on attempt %d/%d: %s",
                        func.__name__, attempt, max_attempts, e,
                    )
                    raise
            raise last_exc or RuntimeError(f"{func.__name__} failed (unknown)")
        return wrapper
    return decorator


def call_with_deadline(fn, timeout: float, default=None, *args, **kwargs):
    """Run `fn` with a hard wall-clock deadline; return `default` if it overruns.

    Uses a DAEMON thread, not ThreadPoolExecutor. The executor registers an
    atexit hook that joins its workers, so a thread abandoned mid-socket-read
    blocks interpreter shutdown — a hung Tor probe stopped a script exiting for
    minutes even though the result had already been computed. A daemon thread is
    simply forgotten and dies with the process.

    Python cannot interrupt a thread blocked in a socket, so the work genuinely
    continues in the background; its result is discarded. Only use this for
    idempotent, side-effect-light work (network reads).
    """
    import threading

    box = {"value": default, "done": False}

    def _runner():
        try:
            box["value"] = fn(*args, **kwargs)
        except Exception as e:                       # noqa: BLE001
            box["error"] = e
        finally:
            box["done"] = True

    t = threading.Thread(target=_runner, daemon=True,
                         name=f"deadline-{getattr(fn, '__name__', 'fn')}")
    t.start()
    t.join(timeout)
    if not box["done"]:
        return default, False                        # still running — abandoned
    if "error" in box:
        raise box["error"]
    return box["value"], True

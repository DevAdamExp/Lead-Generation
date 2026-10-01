"""
utils/logger.py — Centralised logging configuration (Bug #19 fix).
Call setup_logging() once at app startup.
"""
import logging
import sys


def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "playwright", "asyncio", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("backend").setLevel(level)
    logging.getLogger("celery").setLevel(logging.INFO)

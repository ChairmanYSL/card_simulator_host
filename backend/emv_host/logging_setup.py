"""Logging setup: console + rotating file."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(log_dir: str, level: str = "info") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FMT))
    root.addHandler(console)

    try:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path / "host.log", maxBytes=5 * 1024 * 1024, backupCount=5,
            encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FMT))
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - file logging is best effort
        root.warning("could not open log file under %s", log_dir)

    # quieter third-party chatter
    for noisy in ("uvicorn.access", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

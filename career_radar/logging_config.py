from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_local_logging(data_dir: Path) -> Path:
    """Configure bounded local diagnostics without request bodies or secrets."""
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "career-radar.log"
    root = logging.getLogger()
    if not any(isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(path) for handler in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return path

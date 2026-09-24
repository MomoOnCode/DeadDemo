"""Application logging: a rotating file under the data dir, plus stderr for the CLI.

Nothing in the app logged anywhere before this; a failed history refresh vanished with its 12-second
status-bar message. ``setup_logging()`` is called once by the GUI and the CLI entry points."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from deaddemo import paths

_configured = False


def log_path() -> Path:
    d = paths.data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "app.log"


def setup_logging(*, console: bool = False, level: int = logging.INFO) -> Path:
    global _configured
    path = log_path()
    if _configured:
        return path
    root = logging.getLogger("deaddemo")
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    _configured = True
    return path
